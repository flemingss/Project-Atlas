from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from qdrant_client.http import models as qm
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from atlas.config_manager import ConfigManager
from atlas.config_versions import get_active_config_version
from atlas.artifacts import write_bytes, write_json, write_text
from atlas.hitl_ledger import HitlTaskCreateRequest, create_hitl_task
from atlas.llm.registry import ModelRegistry
from atlas.pipeline.ingest import IngestNode
from atlas.pipeline.judge import JudgeNode
from atlas.pipeline.metadata import MetadataNode
from atlas.pipeline.orchestrator import PipelineOrchestrator
from atlas.pipeline.refine import RefineNode
from atlas.pipeline.state import PipelineNode, create_pipeline_context
from atlas.rag.chunking import chunk_markdown_semantic, chunk_text, infer_chunk_features
from atlas.rag.deterministic import deterministic_chunk_id, sha256_hex
from atlas.rag.normalize import normalize_markdown
from atlas.settings import Settings
from atlas.vectorstore.qdrant_store import QdrantStore
from atlas.workflow_ledger import ArtifactRefCreateRequest, WorkflowRunCreateRequest, add_artifact_ref, create_workflow_run
from atlas.workflow_ledger import NodeRunCreateRequest, create_node_run


async def _activate_doc_version(
    *,
    session_factory: sessionmaker[Session],
    store: QdrantStore,
    tenant_id: str,
    project_id: str,
    corpus_id: str,
    doc_id: str,
    doc_version: str,
) -> None:
    """Set the active doc version in DB and in Qdrant payload flags (best-effort).

    Rollback semantics are implemented by filtering search on `is_active_version == True`.
    """

    try:
        from atlas.doc_versions import set_active_doc_version

        with session_factory() as session:
            set_active_doc_version(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                doc_id=doc_id,
                doc_version=doc_version,
                corpus_id=corpus_id,
            )
    except Exception:
        # If DB update fails, we still try to set Qdrant payloads.
        pass

    base_must = [
        qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=tenant_id)),
        qm.FieldCondition(key="project_id", match=qm.MatchValue(value=project_id)),
        qm.FieldCondition(key="corpus_id", match=qm.MatchValue(value=corpus_id)),
        qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id)),
    ]
    version_must = [
        *base_must,
        qm.FieldCondition(key="doc_version", match=qm.MatchValue(value=doc_version)),
    ]

    try:
        # Mark all versions inactive, then mark the selected version active.
        await run_in_threadpool(store.set_payload, payload={"is_active_version": False}, must=base_must)
        await run_in_threadpool(store.set_payload, payload={"is_active_version": True}, must=version_must)
    except Exception:
        pass


def _artifact_ext(*, mime_type: str, filename: str | None) -> str:
    if filename and "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower()
        if len(ext) <= 10:
            return ext
    if mime_type == "application/pdf":
        return ".pdf"
    if mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return ".docx"
    if mime_type == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        return ".pptx"
    if mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        return ".xlsx"
    if mime_type == "application/msword":
        return ".doc"
    if mime_type == "application/vnd.ms-powerpoint":
        return ".ppt"
    if mime_type == "application/vnd.ms-excel":
        return ".xls"
    return ".bin"


def _effective_config_payload(*, config_manager: ConfigManager, session_factory: sessionmaker[Session]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (effective_payload, source_info)."""
    yaml_defaults = config_manager.get()
    with session_factory() as session:
        active = get_active_config_version(session)

    if active is None:
        return {"pipeline": yaml_defaults.pipeline, "models": yaml_defaults.models}, {"config_version_id": None, "config_hash": yaml_defaults.hash}
    return active.payload, {"config_version_id": active.id, "config_hash": active.config_hash}


def _build_orchestrator(*, settings: Settings, models_cfg: dict[str, Any], pipeline_cfg: dict[str, Any]) -> tuple[PipelineOrchestrator, ModelRegistry]:
    registry = ModelRegistry(settings=settings, models_cfg=models_cfg)

    judge = registry.resolve("judge_model")
    refine = registry.resolve("refine_model")
    meta1 = registry.resolve("metadata_tier1_model")

    judge_provider = registry.provider_for(judge.provider_name)
    refine_provider = registry.provider_for(refine.provider_name)
    tier1_provider = registry.provider_for(meta1.provider_name)

    # Tier2 is optional.
    tier2_provider = None
    tier2_model = None
    try:
        meta2 = registry.resolve("metadata_tier2_model")
        tier2_provider = registry.provider_for(meta2.provider_name)
        tier2_model = meta2.model_name
    except Exception:
        meta2 = None  # noqa: F841

    ingest_node = IngestNode()
    judge_node = JudgeNode(provider=judge_provider, model_name=judge.model_name, model_params=judge.params)
    refine_node = RefineNode(
        provider=refine_provider,
        model_name=refine.model_name,
        model_params=refine.params,
        max_retries=int(
            (pipeline_cfg.get("limits", {}) or {}).get(
                "refine_max_retries",
                (pipeline_cfg.get("thresholds", {}) or {}).get("refine_max_retries", 2),
            )
        ),
    )
    metadata_node = MetadataNode(
        tier1_provider=tier1_provider,
        tier1_model=meta1.model_name,
        tier2_provider=tier2_provider,
        tier2_model=tier2_model,
        tier2_cap_per_doc=int(
            (pipeline_cfg.get("limits", {}) or {}).get(
                "tier2_chunk_cap_per_document",
                (pipeline_cfg.get("limits", {}) or {}).get("metadata_tier2_cap_per_doc", 25),
            )
        ),
    )

    orch = PipelineOrchestrator(
        ingest_node=ingest_node,
        judge_node=judge_node,
        refine_node=refine_node,
        metadata_node=metadata_node,
        config=pipeline_cfg,
    )
    return orch, registry


def _json_ref(obj: Any) -> str:
    try:
        return json.dumps(obj, sort_keys=True)
    except Exception:
        return ""


async def ingest_text_via_pipeline(
    *,
    config_manager: ConfigManager,
    session_factory: sessionmaker[Session],
    existing_run_id: int | None = None,
    doc_id: str,
    doc_version: str,
    tenant_id: str,
    project_id: str,
    corpus_id: str,
    text: str,
    source_mime_type: str,
    is_finalized: bool,
    is_sensitive: bool,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    settings = Settings()
    effective, source_info = _effective_config_payload(config_manager=config_manager, session_factory=session_factory)
    pipeline_cfg = effective.get("pipeline", {}) or {}
    models_cfg = effective.get("models", {}) or {}

    limits = (pipeline_cfg.get("limits", {}) or {})
    max_chars = int(limits.get("chunk_max_chars", 1000))

    normalize_cfg = (pipeline_cfg.get("normalize", {}) or {})
    normalize_enabled = bool(normalize_cfg.get("enabled", True))

    chunking_cfg = (pipeline_cfg.get("chunking", {}) or {})
    chunk_strategy = str(chunking_cfg.get("strategy", "semantic"))
    target_tokens = int(chunking_cfg.get("target_tokens", 320))
    max_tokens = int(chunking_cfg.get("max_tokens", 400))

    orch, registry = _build_orchestrator(settings=settings, models_cfg=models_cfg, pipeline_cfg=pipeline_cfg)

    # Create or reuse durable run
    run_id: int
    if existing_run_id is None:
        with session_factory() as session:
            run = create_workflow_run(
                session,
                req=WorkflowRunCreateRequest(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    doc_id=doc_id,
                    doc_version=doc_version,
                    status="running",
                    current_node="ingest",
                    meta={
                        "source_mime_type": source_mime_type,
                        "is_finalized": bool(is_finalized),
                        "is_sensitive": bool(is_sensitive),
                        "corpus_id": corpus_id,
                        "config": source_info,
                        "request_metadata": metadata or {},
                    },
                ),
            )
            run_id = int(run.id)
    else:
        run_id = int(existing_run_id)
        with session_factory() as session:
            from atlas.models import WorkflowRun

            w = session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id)).scalars().first()
            if w is not None:
                w.status = "running"
                w.current_node = "ingest"
                # Keep existing meta but refresh config snapshot.
                m = w.meta or {}
                m["config"] = source_info
                m.setdefault("corpus_id", corpus_id)
                w.meta = m
                session.commit()

    # Ingest (text -> markdown)
    ingest_node = IngestNode()
    ingest_res = await ingest_node.process_text(text=text, mime_type=source_mime_type)
    ctx = create_pipeline_context(
        doc_id=doc_id,
        doc_version=doc_version,
        tenant_id=tenant_id,
        project_id=project_id,
        source_mime_type=source_mime_type,
        max_refine_retries=int(pipeline_cfg.get("thresholds", {}).get("refine_max_retries", 2)),
    )
    ctx.state.markdown_projection = ingest_res.markdown_projection

    ctx = await orch.process_document(ctx)

    # Normalize final markdown before chunking/embedding.
    if normalize_enabled:
        ctx.state.markdown_projection = normalize_markdown(ctx.state.markdown_projection)

    # Persist markdown projection as an artifact (best-effort).
    try:
        md_art = write_text(
            artifacts_dir=Path(settings.atlas_artifacts_dir),
            rel_path=f"runs/{run_id}/ingest/markdown.md",
            text=ctx.state.markdown_projection,
            mime_type="text/markdown",
        )
        with session_factory() as session:
            add_artifact_ref(
                session,
                run_id=run_id,
                req=ArtifactRefCreateRequest(
                    kind="markdown_projection",
                    path=md_art.rel_path,
                    sha256=md_art.sha256,
                    mime_type=md_art.mime_type,
                    meta={},
                ),
            )
    except Exception:
        pass

    # Record node runs best-effort (durable trace)
    try:
        with session_factory() as session:
            create_node_run(
                session,
                run_id=run_id,
                req=NodeRunCreateRequest(
                    node_name="ingest",
                    status="completed",
                    input_ref="text",
                    output_ref=_json_ref(
                        {
                            "parse_profile": str(ingest_res.parse_profile),
                            "docling_schema_version": ingest_res.docling_schema_version,
                        }
                    ),
                ),
            )

            if "judge" in ctx.results:
                create_node_run(
                    session,
                    run_id=run_id,
                    req=NodeRunCreateRequest(
                        node_name="judge",
                        status="completed",
                        input_ref=str(len(ctx.state.markdown_projection)),
                        output_ref=_json_ref(ctx.results.get("judge")),
                    ),
                )

            if "refine" in ctx.results:
                create_node_run(
                    session,
                    run_id=run_id,
                    req=NodeRunCreateRequest(
                        node_name="refine",
                        status="completed",
                        input_ref=_json_ref({"retry": int(ctx.state.refine_retries)}),
                        output_ref=_json_ref(ctx.results.get("refine")),
                    ),
                )

            if "metadata" in ctx.results:
                create_node_run(
                    session,
                    run_id=run_id,
                    req=NodeRunCreateRequest(
                        node_name="metadata",
                        status="completed",
                        input_ref="",
                        output_ref=_json_ref(ctx.results.get("metadata")),
                    ),
                )
    except Exception:
        pass

    # If HITL, create task and pause.
    if PipelineNode(ctx.state.current_node) == PipelineNode.HITL:
        with session_factory() as session:
            from atlas.models import WorkflowRun

            w = session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id)).scalars().first()
            if w is not None:
                w.status = "hitl"
                w.current_node = "hitl"
                session.commit()

            judge_score = float(ctx.state.mean_judge_score or 0.0)
            create_hitl_task(
                session,
                req=HitlTaskCreateRequest(
                    run_id=run_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    doc_id=doc_id,
                    doc_version=doc_version,
                    chunk_id="doc",
                    is_sensitive=bool(is_sensitive),
                    judge_score=judge_score,
                    before_md=ctx.state.markdown_projection,
                    meta={"source": "pipeline", "config": source_info, "corpus_id": corpus_id},
                ),
            )

            try:
                create_node_run(
                    session,
                    run_id=run_id,
                    req=NodeRunCreateRequest(
                        node_name="hitl",
                        status="waiting",
                        input_ref=_json_ref({"judge_score": float(ctx.state.mean_judge_score or 0.0)}),
                        output_ref="",
                    ),
                )
            except Exception:
                pass

        return {
            "ok": True,
            "run_id": run_id,
            "collection": "atlas_chunks",
            "chunks_upserted": 0,
            "paused_for_hitl": True,
        }

    # Commit embeddings/chunks to Qdrant
    resolved_embed = registry.resolve("embed_model")
    embed_provider = registry.provider_for(resolved_embed.provider_name)

    if chunk_strategy == "paragraph":
        chunks = chunk_text(text=ctx.state.markdown_projection, max_chars=max_chars)
    else:
        chunks = chunk_markdown_semantic(
            text=ctx.state.markdown_projection,
            target_tokens=target_tokens,
            max_tokens=max_tokens,
        )
    texts = [c.text for c in chunks]
    if not texts:
        return {"ok": True, "run_id": run_id, "collection": "atlas_chunks", "chunks_upserted": 0}

    vectors = await embed_provider.embed(model=resolved_embed.model_name, texts=texts, params=resolved_embed.params)

    store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection="atlas_chunks")
    await run_in_threadpool(store.ensure_collection, vector_size=len(vectors[0]))

    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")

    judge = ctx.results.get("judge") or {}
    meta = ctx.results.get("metadata") or {}

    points: list[qm.PointStruct] = []
    manifest_lines: list[str] = []
    for c, v in zip(chunks, vectors, strict=True):
        content_hash = sha256_hex(c.text)
        pid = deterministic_chunk_id(
            tenant_id=tenant_id,
            project_id=project_id,
            corpus_id=corpus_id,
            doc_id=doc_id,
            doc_version=doc_version,
            content_hash=content_hash,
            chunk_index=c.index,
        )
        feats = infer_chunk_features(c.text)
        payload: dict[str, Any] = {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "corpus_id": corpus_id,
            "doc_id": doc_id,
            "doc_version": doc_version,
            "chunk_index": c.index,
            "text": c.text,
            "content_hash": content_hash,
            "is_finalized": bool(is_finalized),
            "is_sensitive": bool(is_sensitive),
            "is_active_version": True,
            "source_mime_type": source_mime_type,
            "section_path": getattr(c, "section_path", []) or [],
            "parent_header_id": getattr(c, "parent_header_id", None),
            "sibling_ids": getattr(c, "sibling_ids", []) or [],
            "has_table": bool(feats.has_table),
            "is_procedure": bool(feats.is_procedure),
            "has_code": bool(feats.has_code),
            "embedding_provider": resolved_embed.provider_name,
            "embedding_model": resolved_embed.model_name,
            "embedding_params": resolved_embed.params,
            "judge_score": judge.get("score"),
            "judge_version": judge.get("judge_version"),
            "confidence_rationale": judge.get("confidence_rationale"),
            "metadata_tier": meta.get("tier"),
            "metadata_tags": meta.get("tags"),
            "created_at": now,
            **(metadata or {}),
        }
        points.append(qm.PointStruct(id=pid, vector=v, payload=payload))

        manifest_lines.append(
            json.dumps(
                {
                    "chunk_id": pid,
                    "doc_id": doc_id,
                    "doc_version": doc_version,
                    "chunk_index": c.index,
                    "heading_path": payload.get("section_path") or [],
                    "text": c.text,
                    "metadata": {
                        "tenant_id": tenant_id,
                        "project_id": project_id,
                        "corpus_id": corpus_id,
                        "source_mime_type": source_mime_type,
                        "has_table": bool(feats.has_table),
                        "is_procedure": bool(feats.is_procedure),
                        "has_code": bool(feats.has_code),
                        "embedding_model": resolved_embed.model_name,
                        "embedding_provider": resolved_embed.provider_name,
                        "embedding_params": resolved_embed.params,
                        "metadata_tags": meta.get("tags"),
                    },
                },
                ensure_ascii=False,
            )
        )

    await run_in_threadpool(store.upsert_points, points=points)

    # Persist chunk manifest as an artifact (best-effort).
    try:
        mf_art = write_text(
            artifacts_dir=Path(settings.atlas_artifacts_dir),
            rel_path=f"runs/{run_id}/chunks/manifest.jsonl",
            text="\n".join(manifest_lines) + "\n",
            mime_type="application/x-ndjson",
        )
        with session_factory() as session:
            add_artifact_ref(
                session,
                run_id=run_id,
                req=ArtifactRefCreateRequest(
                    kind="chunk_manifest",
                    path=mf_art.rel_path,
                    sha256=mf_art.sha256,
                    mime_type=mf_art.mime_type,
                    meta={"chunks": len(points)},
                ),
            )
    except Exception:
        pass

    # Activate this doc_version for the doc (best-effort).
    await _activate_doc_version(
        session_factory=session_factory,
        store=store,
        tenant_id=tenant_id,
        project_id=project_id,
        corpus_id=corpus_id,
        doc_id=doc_id,
        doc_version=doc_version,
    )

    with session_factory() as session:
        from atlas.models import WorkflowRun

        w = session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id)).scalars().first()
        if w is not None:
            w.status = "completed"
            w.current_node = "completed"
            session.commit()

        try:
            create_node_run(
                session,
                run_id=run_id,
                req=NodeRunCreateRequest(
                    node_name="commit",
                    status="completed",
                    input_ref=_json_ref({"collection": store.collection}),
                    output_ref=_json_ref({"chunks_upserted": len(points)}),
                ),
            )
        except Exception:
            pass

    return {"ok": True, "run_id": run_id, "collection": store.collection, "chunks_upserted": len(points)}


async def ingest_file_via_pipeline(
    *,
    config_manager: ConfigManager,
    session_factory: sessionmaker[Session],
    doc_id: str,
    doc_version: str,
    tenant_id: str,
    project_id: str,
    corpus_id: str,
    file_bytes: bytes,
    filename: str | None,
    source_mime_type: str,
    is_finalized: bool,
    is_sensitive: bool,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    settings = Settings()
    artifacts_dir = Path(settings.atlas_artifacts_dir)
    effective, source_info = _effective_config_payload(config_manager=config_manager, session_factory=session_factory)
    pipeline_cfg = effective.get("pipeline", {}) or {}
    models_cfg = effective.get("models", {}) or {}

    limits = (pipeline_cfg.get("limits", {}) or {})
    max_chars = int(limits.get("chunk_max_chars", 1000))

    normalize_cfg = (pipeline_cfg.get("normalize", {}) or {})
    normalize_enabled = bool(normalize_cfg.get("enabled", True))

    chunking_cfg = (pipeline_cfg.get("chunking", {}) or {})
    chunk_strategy = str(chunking_cfg.get("strategy", "semantic"))
    target_tokens = int(chunking_cfg.get("target_tokens", 320))
    max_tokens = int(chunking_cfg.get("max_tokens", 400))

    orch, registry = _build_orchestrator(settings=settings, models_cfg=models_cfg, pipeline_cfg=pipeline_cfg)

    # Durable run
    with session_factory() as session:
        run = create_workflow_run(
            session,
            req=WorkflowRunCreateRequest(
                tenant_id=tenant_id,
                project_id=project_id,
                doc_id=doc_id,
                doc_version=doc_version,
                status="running",
                current_node="ingest",
                meta={
                    "source_mime_type": source_mime_type,
                    "source_filename": filename or "",
                    "is_finalized": bool(is_finalized),
                    "is_sensitive": bool(is_sensitive),
                    "corpus_id": corpus_id,
                    "config": source_info,
                    "request_metadata": metadata or {},
                },
            ),
        )
        run_id = int(run.id)

    # Store source artifact (best-effort)
    try:
        ext = _artifact_ext(mime_type=source_mime_type, filename=filename)
        src = write_bytes(
            artifacts_dir=artifacts_dir,
            rel_path=f"runs/{run_id}/source/{doc_id}_{doc_version}{ext}",
            data=file_bytes,
            mime_type=source_mime_type,
        )
        with session_factory() as session:
            add_artifact_ref(
                session,
                run_id=run_id,
                req=ArtifactRefCreateRequest(
                    kind="source",
                    path=src.rel_path,
                    sha256=src.sha256,
                    mime_type=src.mime_type,
                    meta={"filename": filename or "", "doc_id": doc_id, "doc_version": doc_version},
                ),
            )
    except Exception:
        pass

    # Ingest via Docling
    ingest_node = IngestNode()
    ingest_res = await ingest_node.process_document(content=file_bytes, mime_type=source_mime_type)

    # Record ingest node run and persist artifacts (best-effort)
    ingest_node_run_id: int | None = None
    try:
        with session_factory() as session:
            nr = create_node_run(
                session,
                run_id=run_id,
                req=NodeRunCreateRequest(
                    node_name="ingest",
                    status="completed" if ingest_res.success else "failed",
                    input_ref=_json_ref({"mime_type": source_mime_type, "filename": filename or ""}),
                    output_ref=_json_ref(
                        {
                            "parse_profile": str(ingest_res.parse_profile),
                            "docling_schema_version": ingest_res.docling_schema_version,
                            "meta": ingest_res.meta or {},
                        }
                    ),
                    error_code="" if ingest_res.error_code is None else ingest_res.error_code.value,
                    error_message=ingest_res.error_message or "",
                ),
            )
            ingest_node_run_id = int(nr.id)

        # Persist ground truth + projection
        docling_art = write_json(
            artifacts_dir=artifacts_dir,
            rel_path=f"runs/{run_id}/ingest/docling.json",
            obj=ingest_res.docling_json,
        )
        md_art = write_text(
            artifacts_dir=artifacts_dir,
            rel_path=f"runs/{run_id}/ingest/markdown.md",
            text=ingest_res.markdown_projection,
            mime_type="text/markdown",
        )
        with session_factory() as session:
            add_artifact_ref(
                session,
                run_id=run_id,
                req=ArtifactRefCreateRequest(
                    kind="docling_json",
                    path=docling_art.rel_path,
                    node_run_id=ingest_node_run_id,
                    sha256=docling_art.sha256,
                    mime_type=docling_art.mime_type,
                    meta={"docling_schema_version": ingest_res.docling_schema_version},
                ),
            )
            add_artifact_ref(
                session,
                run_id=run_id,
                req=ArtifactRefCreateRequest(
                    kind="markdown_projection",
                    path=md_art.rel_path,
                    node_run_id=ingest_node_run_id,
                    sha256=md_art.sha256,
                    mime_type=md_art.mime_type,
                    meta={"ingest_meta": ingest_res.meta or {}},
                ),
            )
    except Exception:
        pass

    if not ingest_res.success:
        with session_factory() as session:
            from atlas.models import WorkflowRun

            w = session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id)).scalars().first()
            if w is not None:
                w.status = "failed"
                w.current_node = "ingest"
                w.error_code = "" if ingest_res.error_code is None else ingest_res.error_code.value
                w.error_message = ingest_res.error_message or ""
                session.commit()
            return {
                "ok": False,
                "run_id": run_id,
                "collection": "atlas_chunks",
                "chunks_upserted": 0,
                "error_code": "" if ingest_res.error_code is None else ingest_res.error_code.value,
                "error_message": ingest_res.error_message or "",
            }

    ctx = create_pipeline_context(
        doc_id=doc_id,
        doc_version=doc_version,
        tenant_id=tenant_id,
        project_id=project_id,
        source_mime_type=source_mime_type,
        max_refine_retries=int(pipeline_cfg.get("thresholds", {}).get("refine_max_retries", 2)),
    )
    ctx.state.markdown_projection = ingest_res.markdown_projection
    ctx = await orch.process_document(ctx)

    # Normalize final markdown before chunking/embedding.
    if normalize_enabled:
        ctx.state.markdown_projection = normalize_markdown(ctx.state.markdown_projection)

    # If HITL, create task and pause.
    if PipelineNode(ctx.state.current_node) == PipelineNode.HITL:
        with session_factory() as session:
            from atlas.models import WorkflowRun

            w = session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id)).scalars().first()
            if w is not None:
                w.status = "hitl"
                w.current_node = "hitl"
                session.commit()

            judge_score = float(ctx.state.mean_judge_score or 0.0)
            create_hitl_task(
                session,
                req=HitlTaskCreateRequest(
                    run_id=run_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    doc_id=doc_id,
                    doc_version=doc_version,
                    chunk_id="doc",
                    is_sensitive=bool(is_sensitive),
                    judge_score=judge_score,
                    before_md=ctx.state.markdown_projection,
                    meta={"source": "pipeline", "config": source_info, "corpus_id": corpus_id},
                ),
            )
            try:
                create_node_run(
                    session,
                    run_id=run_id,
                    req=NodeRunCreateRequest(
                        node_name="hitl",
                        status="waiting",
                        input_ref=_json_ref({"judge_score": float(ctx.state.mean_judge_score or 0.0)}),
                        output_ref="",
                    ),
                )
            except Exception:
                pass

        return {
            "ok": True,
            "run_id": run_id,
            "collection": "atlas_chunks",
            "chunks_upserted": 0,
            "paused_for_hitl": True,
        }

    # Commit embeddings/chunks to Qdrant
    resolved_embed = registry.resolve("embed_model")
    embed_provider = registry.provider_for(resolved_embed.provider_name)

    if chunk_strategy == "paragraph":
        chunks = chunk_text(text=ctx.state.markdown_projection, max_chars=max_chars)
    else:
        chunks = chunk_markdown_semantic(
            text=ctx.state.markdown_projection,
            target_tokens=target_tokens,
            max_tokens=max_tokens,
        )
    texts = [c.text for c in chunks]
    if not texts:
        return {"ok": True, "run_id": run_id, "collection": "atlas_chunks", "chunks_upserted": 0}

    vectors = await embed_provider.embed(model=resolved_embed.model_name, texts=texts, params=resolved_embed.params)

    store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection="atlas_chunks")
    await run_in_threadpool(store.ensure_collection, vector_size=len(vectors[0]))

    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")

    judge = ctx.results.get("judge") or {}
    meta = ctx.results.get("metadata") or {}

    points: list[qm.PointStruct] = []
    manifest_lines: list[str] = []
    for c, v in zip(chunks, vectors, strict=True):
        content_hash = sha256_hex(c.text)
        pid = deterministic_chunk_id(
            tenant_id=tenant_id,
            project_id=project_id,
            corpus_id=corpus_id,
            doc_id=doc_id,
            doc_version=doc_version,
            content_hash=content_hash,
            chunk_index=c.index,
        )
        feats = infer_chunk_features(c.text)
        payload: dict[str, Any] = {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "corpus_id": corpus_id,
            "doc_id": doc_id,
            "doc_version": doc_version,
            "chunk_index": c.index,
            "text": c.text,
            "content_hash": content_hash,
            "is_finalized": bool(is_finalized),
            "is_sensitive": bool(is_sensitive),
            "is_active_version": True,
            "source_mime_type": source_mime_type,
            "source_filename": filename or "",
            "section_path": getattr(c, "section_path", []) or [],
            "parent_header_id": getattr(c, "parent_header_id", None),
            "sibling_ids": getattr(c, "sibling_ids", []) or [],
            "has_table": bool(feats.has_table),
            "is_procedure": bool(feats.is_procedure),
            "has_code": bool(feats.has_code),
            "embedding_provider": resolved_embed.provider_name,
            "embedding_model": resolved_embed.model_name,
            "embedding_params": resolved_embed.params,
            "judge_score": judge.get("score"),
            "judge_version": judge.get("judge_version"),
            "confidence_rationale": judge.get("confidence_rationale"),
            "metadata_tier": meta.get("tier"),
            "metadata_tags": meta.get("tags"),
            "created_at": now,
            **(metadata or {}),
        }
        points.append(qm.PointStruct(id=pid, vector=v, payload=payload))

        manifest_lines.append(
            json.dumps(
                {
                    "chunk_id": pid,
                    "doc_id": doc_id,
                    "doc_version": doc_version,
                    "chunk_index": c.index,
                    "heading_path": payload.get("section_path") or [],
                    "text": c.text,
                    "metadata": {
                        "tenant_id": tenant_id,
                        "project_id": project_id,
                        "corpus_id": corpus_id,
                        "source_mime_type": source_mime_type,
                        "source_filename": filename or "",
                        "has_table": bool(feats.has_table),
                        "is_procedure": bool(feats.is_procedure),
                        "has_code": bool(feats.has_code),
                        "embedding_model": resolved_embed.model_name,
                        "embedding_provider": resolved_embed.provider_name,
                        "embedding_params": resolved_embed.params,
                        "metadata_tags": meta.get("tags"),
                    },
                },
                ensure_ascii=False,
            )
        )

    await run_in_threadpool(store.upsert_points, points=points)

    # Persist chunk manifest as an artifact (best-effort).
    try:
        mf_art = write_text(
            artifacts_dir=Path(settings.atlas_artifacts_dir),
            rel_path=f"runs/{run_id}/chunks/manifest.jsonl",
            text="\n".join(manifest_lines) + "\n",
            mime_type="application/x-ndjson",
        )
        with session_factory() as session:
            add_artifact_ref(
                session,
                run_id=run_id,
                req=ArtifactRefCreateRequest(
                    kind="chunk_manifest",
                    path=mf_art.rel_path,
                    sha256=mf_art.sha256,
                    mime_type=mf_art.mime_type,
                    meta={"chunks": len(points)},
                ),
            )
    except Exception:
        pass

    # Activate this doc_version for the doc (best-effort).
    await _activate_doc_version(
        session_factory=session_factory,
        store=store,
        tenant_id=tenant_id,
        project_id=project_id,
        corpus_id=corpus_id,
        doc_id=doc_id,
        doc_version=doc_version,
    )

    with session_factory() as session:
        from atlas.models import WorkflowRun

        w = session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id)).scalars().first()
        if w is not None:
            w.status = "completed"
            w.current_node = "completed"
            session.commit()

        try:
            create_node_run(
                session,
                run_id=run_id,
                req=NodeRunCreateRequest(
                    node_name="commit",
                    status="completed",
                    input_ref=_json_ref({"collection": store.collection}),
                    output_ref=_json_ref({"chunks_upserted": len(points)}),
                ),
            )
        except Exception:
            pass

    return {"ok": True, "run_id": run_id, "collection": store.collection, "chunks_upserted": len(points)}


async def resume_completed_hitl_task(
    *,
    config_manager: ConfigManager,
    session_factory: sessionmaker[Session],
    task_id: int,
) -> dict[str, Any]:
    """Resume a pipeline run from a completed HITL task and commit finalized chunks."""
    from atlas.hitl_ledger import get_hitl_task
    from atlas.models import WorkflowRun

    with session_factory() as session:
        task = get_hitl_task(session, task_id=task_id)
        if task is None:
            raise KeyError("task not found")
        if task.status != "completed":
            raise ValueError("task must be completed to resume")

        run = session.execute(select(WorkflowRun).where(WorkflowRun.id == task.run_id)).scalars().first()
        if run is None:
            raise KeyError("run not found")

        settings = Settings()
        is_finalized = bool((run.meta or {}).get("is_finalized", True))
        source_mime_type = str((run.meta or {}).get("source_mime_type", "text/plain"))
        corpus_id = str((run.meta or {}).get("corpus_id") or settings.atlas_default_corpus_id)
        req_meta = (run.meta or {}).get("request_metadata", {})
        if not isinstance(req_meta, dict):
            req_meta = {}

        markdown = task.after_md or ""
        if markdown.strip() == "":
            raise ValueError("task has empty after_md")

        run.status = "running"
        run.current_node = "ingest"
        session.commit()

    # Re-run pipeline + commit using the existing run_id.
    return await ingest_text_via_pipeline(
        config_manager=config_manager,
        session_factory=session_factory,
        existing_run_id=int(task.run_id),
        doc_id=str(task.doc_id),
        doc_version=str(task.doc_version),
        tenant_id=str(task.tenant_id),
        project_id=str(task.project_id),
        corpus_id=corpus_id,
        text=markdown,
        source_mime_type=source_mime_type,
        is_finalized=is_finalized,
        is_sensitive=bool(task.is_sensitive),
        metadata=req_meta,
    )
