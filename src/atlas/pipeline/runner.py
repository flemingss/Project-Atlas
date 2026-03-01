from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any

from qdrant_client.http import models as qm
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from atlas.config_manager import ConfigManager
from atlas.config_versions import get_active_config_version
from atlas.artifacts import write_bytes, write_json, write_text
from atlas.doc_versions import set_active_doc_version
from atlas.hitl_ledger import HitlTaskCreateRequest, create_hitl_task
from atlas.ingest.docling_health import compute_health as _compute_health
from atlas.llm.registry import ModelRegistry
from atlas.models import ArtifactRef as ArtifactRefModel, WorkflowRun
from atlas.pipeline.ingest import IngestNode
from atlas.pipeline.judge import JudgeNode
from atlas.pipeline.metadata import MetadataNode
from atlas.pipeline.orchestrator import PipelineOrchestrator
from atlas.pipeline.refine import RefineNode
from atlas.pipeline.state import PipelineNode, create_pipeline_context
from atlas.rag.chunk_qa import chunk_with_fallback
from atlas.rag.chunking import chunk_markdown_semantic, chunk_text, chunk_text_hierarchical, infer_chunk_features
from atlas.rag.deterministic import deterministic_chunk_id, sha256_hex
from atlas.rag.normalize import normalize_markdown
from atlas.schemas import FidelityFlag
from atlas.settings import Settings
from atlas.vectorstore.qdrant_store import QdrantStore
from atlas.workflow_ledger import ArtifactRefCreateRequest, WorkflowRunCreateRequest, add_artifact_ref, create_workflow_run
from atlas.workflow_ledger import NodeRunCreateRequest, create_node_run

log = logging.getLogger(__name__)


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
        log.warning("Failed to set active doc version in DB", exc_info=True)

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
        log.warning("Failed to set active doc version in Qdrant", exc_info=True)


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
        pass

    ingest_node = IngestNode(pdf_parser_config=pipeline_cfg.get("pdf_parser") or {})
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
        min_preservation_ratio=float(
            (pipeline_cfg.get("thresholds", {}) or {}).get(
                "refine_min_preservation_ratio", 0.6
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


def _compute_fidelity_flag(*, judge: dict[str, Any], refine_retries: int, max_retries: int) -> str:
    """Compute fidelity flag string from judge score and refine retry count."""
    judge_score = int(judge.get("score") or 0) if judge else 0
    if refine_retries >= max_retries:
        return FidelityFlag.NEEDS_REVIEW.value
    if not judge:
        return FidelityFlag.VERIFIED.value  # No judge ran — pass-through, treat as verified
    if judge_score >= 4:
        return FidelityFlag.VERIFIED.value
    if judge_score <= 2:
        return FidelityFlag.LOW_CONFIDENCE.value
    return FidelityFlag.PARTIAL.value


# ---------------------------------------------------------------------------
# Shared post-orchestration helpers (used by both ingestion paths)
# ---------------------------------------------------------------------------

def _record_pipeline_node_runs(
    *,
    session_factory: sessionmaker[Session],
    run_id: int,
    ctx: Any,
) -> None:
    """Persist node run records for judge/refine/metadata/cleanup (best-effort)."""
    try:
        with session_factory() as session:
            if "cleanup" in ctx.results:
                create_node_run(
                    session,
                    run_id=run_id,
                    req=NodeRunCreateRequest(
                        node_name="cleanup",
                        status="completed",
                        input_ref=str(ctx.results["cleanup"].get("chars_before", "")),
                        output_ref=_json_ref(ctx.results.get("cleanup")),
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
        log.warning("Failed to record pipeline node runs for run %s", run_id, exc_info=True)


def _record_normalize_node_run(
    *,
    session_factory: sessionmaker[Session],
    run_id: int,
    chars_before: int,
    chars_after: int,
) -> None:
    """Persist a node run record for the normalize step (best-effort)."""
    try:
        with session_factory() as session:
            create_node_run(
                session,
                run_id=run_id,
                req=NodeRunCreateRequest(
                    node_name="normalize",
                    status="completed",
                    input_ref=str(chars_before),
                    output_ref=_json_ref({"chars_before": chars_before, "chars_after": chars_after}),
                ),
            )
    except Exception:
        log.warning("Failed to record normalize node run for run %s", run_id, exc_info=True)


def _persist_markdown_artifact(
    *,
    session_factory: sessionmaker[Session],
    artifacts_dir: Path,
    run_id: int,
    text: str,
    update_existing: bool = False,
) -> None:
    """Write (or overwrite) the markdown projection artifact and its DB ref."""
    try:
        md_art = write_text(
            artifacts_dir=artifacts_dir,
            rel_path=f"runs/{run_id}/ingest/markdown.md",
            text=text,
            mime_type="text/markdown",
        )
        with session_factory() as session:
            if update_existing:
                existing = session.execute(
                    select(ArtifactRefModel).where(
                        ArtifactRefModel.run_id == run_id,
                        ArtifactRefModel.kind == "markdown_projection",
                    )
                ).scalars().first()
                if existing is not None:
                    existing.sha256 = md_art.sha256
                    session.commit()
                    return
            # Fresh insert
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
        log.warning("Failed to persist markdown artifact for run %s", run_id, exc_info=True)


def _handle_hitl_pause(
    *,
    session_factory: sessionmaker[Session],
    run_id: int,
    ctx: Any,
    tenant_id: str,
    project_id: str,
    doc_id: str,
    doc_version: str,
    is_sensitive: bool,
    source_info: dict[str, Any],
    corpus_id: str,
) -> dict[str, Any]:
    """Create HITL task, update workflow status, return paused response."""
    with session_factory() as session:
        w = session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id)).scalars().first()
        if w is not None:
            w.status = "hitl"
            w.current_node = "hitl"
            session.commit()

        judge_score = float(ctx.state.mean_judge_score or 0.0)

        # Build rich HITL context from pipeline results so human reviewers
        # see exactly what the LLM judge found and what refine attempted.
        judge_result = ctx.results.get("judge", {})
        hitl_meta: dict[str, Any] = {
            "source": "pipeline",
            "config": source_info,
            "corpus_id": corpus_id,
            "judge_sub_scores": judge_result.get("sub_scores", {}),
            "judge_rationale": judge_result.get("confidence_rationale", ""),
            "judge_score_history": ctx.results.get("judge_score_history", []),
            "refine_retries": ctx.state.refine_retries,
            "refine_total_attempts": ctx.results.get("refine_total_attempts", 0),
        }
        # Include routing reason if available
        refine_result = ctx.results.get("refine", {})
        if refine_result:
            hitl_meta["last_refine_improvements"] = refine_result.get("improvements_made", [])
            hitl_meta["last_refine_success"] = refine_result.get("success", False)

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
                meta=hitl_meta,
            ),
        )

        try:
            create_node_run(
                session,
                run_id=run_id,
                req=NodeRunCreateRequest(
                    node_name="hitl",
                    status="waiting",
                    input_ref=_json_ref({"judge_score": judge_score}),
                    output_ref="",
                ),
            )
        except Exception:
            log.warning("Failed to record HITL node run for run %s", run_id, exc_info=True)

    return {
        "ok": True,
        "run_id": run_id,
        "collection": "atlas_chunks",
        "chunks_upserted": 0,
        "paused_for_hitl": True,
    }


async def _commit_chunks_to_qdrant(
    *,
    session_factory: sessionmaker[Session],
    settings: Settings,
    registry: ModelRegistry,
    ctx: Any,
    run_id: int,
    pipeline_cfg: dict[str, Any],
    chunking_cfg: dict[str, Any],
    tenant_id: str,
    project_id: str,
    corpus_id: str,
    doc_id: str,
    doc_version: str,
    source_mime_type: str,
    source_filename: str,
    is_finalized: bool,
    is_sensitive: bool,
    metadata: dict[str, Any],
    source_info: dict[str, Any],
) -> dict[str, Any]:
    """Embed, chunk, purge stale, upsert to Qdrant, and finalize the run."""

    chunk_strategy = str(chunking_cfg.get("strategy", "semantic"))
    target_tokens = int(chunking_cfg.get("target_tokens", 320))
    max_tokens = int(chunking_cfg.get("max_tokens", 400))
    max_chars = int((pipeline_cfg.get("limits", {}) or {}).get("chunk_max_chars", 1000))

    resolved_embed = registry.resolve("embed_model")
    embed_provider = registry.provider_for(resolved_embed.provider_name)

    qa_bounds = (chunking_cfg.get("qa") or {}) if chunking_cfg else {}
    chunks, strategy_used, chunk_qa = chunk_with_fallback(
        text=ctx.state.markdown_projection,
        strategy=chunk_strategy,
        target_tokens=target_tokens,
        max_tokens=max_tokens,
        max_chars=max_chars,
        qa_bounds=qa_bounds if qa_bounds else None,
    )
    texts = [c.text for c in chunks]
    if not texts:
        return {"ok": True, "run_id": run_id, "collection": "atlas_chunks", "chunks_upserted": 0}

    vectors = await embed_provider.embed(model=resolved_embed.model_name, texts=texts, params=resolved_embed.params)

    store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection="atlas_chunks")
    await run_in_threadpool(store.ensure_collection, vector_size=len(vectors[0]))

    # Purge stale chunks for this doc_id + doc_version before upserting.
    stale_filter = [
        qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=tenant_id)),
        qm.FieldCondition(key="project_id", match=qm.MatchValue(value=project_id)),
        qm.FieldCondition(key="corpus_id", match=qm.MatchValue(value=corpus_id)),
        qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id)),
        qm.FieldCondition(key="doc_version", match=qm.MatchValue(value=doc_version)),
    ]
    try:
        await run_in_threadpool(store.delete_by_filter, must=stale_filter)
    except Exception:
        log.warning("Failed to purge stale chunks for run %s", run_id, exc_info=True)

    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")

    judge = ctx.results.get("judge") or {}
    meta = ctx.results.get("metadata") or {}
    limits_cfg = pipeline_cfg.get("limits") or {}
    thresholds_cfg = pipeline_cfg.get("thresholds") or {}
    max_retries_cfg = int(
        limits_cfg.get("refine_max_retries", thresholds_cfg.get("refine_max_retries", 2))
    )
    fidelity_flag = _compute_fidelity_flag(
        judge=judge,
        refine_retries=int(ctx.state.refine_retries),
        max_retries=max_retries_cfg,
    )

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
            "source_filename": source_filename,
            "chunking_strategy": strategy_used,
            "chunk_qa": chunk_qa.to_dict(),
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
            "fidelity_flag": fidelity_flag,
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
                        "source_filename": source_filename,
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
        log.warning("Failed to persist chunk manifest for run %s", run_id, exc_info=True)

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
            log.warning("Failed to record commit node run for run %s", run_id, exc_info=True)

    return {"ok": True, "run_id": run_id, "collection": store.collection, "chunks_upserted": len(points)}


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
    is_hitl_resume: bool = False,
) -> dict[str, Any]:
    settings = Settings()
    artifacts_dir = Path(settings.atlas_artifacts_dir)
    effective, source_info = _effective_config_payload(config_manager=config_manager, session_factory=session_factory)
    pipeline_cfg = effective.get("pipeline", {}) or {}
    models_cfg = effective.get("models", {}) or {}

    normalize_cfg = (pipeline_cfg.get("normalize", {}) or {})
    normalize_enabled = bool(normalize_cfg.get("enabled", True))

    chunking_cfg = (pipeline_cfg.get("chunking", {}) or {})

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
        corpus_id=corpus_id,
        source_mime_type=source_mime_type,
        max_refine_retries=int(
            (pipeline_cfg.get("limits") or {}).get(
                "refine_max_retries",
                (pipeline_cfg.get("thresholds") or {}).get("refine_max_retries", 2),
            )
        ),
    )
    ctx.state.markdown_projection = ingest_res.markdown_projection
    ctx.state.parse_profile = ingest_res.parse_profile

    # Compute Docling health score for downstream routing/diagnostics.
    _health = _compute_health(
        meta=ingest_res.meta,
        markdown_length=len(ingest_res.markdown_projection),
        parse_profile=str(ingest_res.parse_profile),
    )
    ctx.set_docling_health(_health.to_dict())

    # When resuming from a completed HITL task, mark context so that
    # routing skips cleanup-rejudge / refine loops — the human already
    # approved the markdown.
    if is_hitl_resume:
        ctx.results["is_hitl_resume"] = True

    ctx = await orch.process_document(ctx)

    # Normalize final markdown before chunking/embedding.
    if normalize_enabled:
        chars_before = len(ctx.state.markdown_projection)
        ctx.state.markdown_projection = normalize_markdown(ctx.state.markdown_projection)
        _record_normalize_node_run(
            session_factory=session_factory,
            run_id=run_id,
            chars_before=chars_before,
            chars_after=len(ctx.state.markdown_projection),
        )

    # Persist markdown projection as an artifact.
    _persist_markdown_artifact(
        session_factory=session_factory,
        artifacts_dir=artifacts_dir,
        run_id=run_id,
        text=ctx.state.markdown_projection,
    )

    # Record ingest node run (best-effort).
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
    except Exception:
        log.warning("Failed to record ingest node run for run %s", run_id, exc_info=True)

    # Record pipeline node runs (cleanup/judge/refine/metadata).
    _record_pipeline_node_runs(session_factory=session_factory, run_id=run_id, ctx=ctx)

    # If HITL, create task and pause.
    if PipelineNode(ctx.state.current_node) == PipelineNode.HITL:
        return _handle_hitl_pause(
            session_factory=session_factory,
            run_id=run_id,
            ctx=ctx,
            tenant_id=tenant_id,
            project_id=project_id,
            doc_id=doc_id,
            doc_version=doc_version,
            is_sensitive=is_sensitive,
            source_info=source_info,
            corpus_id=corpus_id,
        )

    # Commit embeddings/chunks to Qdrant.
    return await _commit_chunks_to_qdrant(
        session_factory=session_factory,
        settings=settings,
        registry=registry,
        ctx=ctx,
        run_id=run_id,
        pipeline_cfg=pipeline_cfg,
        chunking_cfg=chunking_cfg,
        tenant_id=tenant_id,
        project_id=project_id,
        corpus_id=corpus_id,
        doc_id=doc_id,
        doc_version=doc_version,
        source_mime_type=source_mime_type,
        source_filename="",
        is_finalized=is_finalized,
        is_sensitive=is_sensitive,
        metadata=metadata,
        source_info=source_info,
    )


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

    normalize_cfg = (pipeline_cfg.get("normalize", {}) or {})
    normalize_enabled = bool(normalize_cfg.get("enabled", True))

    chunking_cfg = (pipeline_cfg.get("chunking", {}) or {})

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
        log.warning("Failed to store source artifact for run %s", run_id, exc_info=True)

    # Ingest via configured parser (pdf_parser.backend in pipeline.yaml)
    ingest_node = IngestNode(pdf_parser_config=pipeline_cfg.get("pdf_parser") or {})
    ingest_res = await ingest_node.process_document(content=file_bytes, mime_type=source_mime_type)

    # Record ingest node run and persist docling artifacts (best-effort)
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

        # Persist docling.json ground truth + initial markdown projection
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
        log.warning("Failed to record ingest node/artifacts for run %s", run_id, exc_info=True)

    if not ingest_res.success:
        with session_factory() as session:
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
        corpus_id=corpus_id,
        source_mime_type=source_mime_type,
        max_refine_retries=int(
            (pipeline_cfg.get("limits") or {}).get(
                "refine_max_retries",
                (pipeline_cfg.get("thresholds") or {}).get("refine_max_retries", 2),
            )
        ),
    )
    ctx.state.markdown_projection = ingest_res.markdown_projection
    ctx.state.parse_profile = ingest_res.parse_profile

    # Compute Docling health score for downstream routing/diagnostics.
    _health = _compute_health(
        meta=ingest_res.meta,
        markdown_length=len(ingest_res.markdown_projection),
        parse_profile=str(ingest_res.parse_profile),
    )
    ctx.set_docling_health(_health.to_dict())

    # Surface extraction metadata (layout parser confidence, backend) into
    # routing context so the router can make backend-aware decisions.
    if ingest_res.meta:
        ctx.results["extraction_meta"] = {
            k: v for k, v in ingest_res.meta.items()
            if k in (
                "extraction_backend", "mean_ocr_confidence",
                "layout_confidence", "ocr_coverage", "estimated_is_scanned",
            )
        }

    ctx = await orch.process_document(ctx)

    # Normalize final markdown before chunking/embedding.
    if normalize_enabled:
        chars_before = len(ctx.state.markdown_projection)
        ctx.state.markdown_projection = normalize_markdown(ctx.state.markdown_projection)
        _record_normalize_node_run(
            session_factory=session_factory,
            run_id=run_id,
            chars_before=chars_before,
            chars_after=len(ctx.state.markdown_projection),
        )

    # Overwrite the markdown artifact with the post-cleanup/normalized text.
    _persist_markdown_artifact(
        session_factory=session_factory,
        artifacts_dir=artifacts_dir,
        run_id=run_id,
        text=ctx.state.markdown_projection,
        update_existing=True,
    )

    # Record pipeline node runs (cleanup/judge/refine/metadata).
    _record_pipeline_node_runs(session_factory=session_factory, run_id=run_id, ctx=ctx)

    # Capture extraction metadata for the API response so the UI can show
    # which backend was used and OCR confidence metrics.
    _extraction_meta = ctx.results.get("extraction_meta") or {}

    # If HITL, create task and pause.
    if PipelineNode(ctx.state.current_node) == PipelineNode.HITL:
        _hitl_result = _handle_hitl_pause(
            session_factory=session_factory,
            run_id=run_id,
            ctx=ctx,
            tenant_id=tenant_id,
            project_id=project_id,
            doc_id=doc_id,
            doc_version=doc_version,
            is_sensitive=is_sensitive,
            source_info=source_info,
            corpus_id=corpus_id,
        )
        if _extraction_meta:
            _hitl_result["extraction_meta"] = _extraction_meta
        return _hitl_result

    # Commit embeddings/chunks to Qdrant.
    _commit_result = await _commit_chunks_to_qdrant(
        session_factory=session_factory,
        settings=settings,
        registry=registry,
        ctx=ctx,
        run_id=run_id,
        pipeline_cfg=pipeline_cfg,
        chunking_cfg=chunking_cfg,
        tenant_id=tenant_id,
        project_id=project_id,
        corpus_id=corpus_id,
        doc_id=doc_id,
        doc_version=doc_version,
        source_mime_type=source_mime_type,
        source_filename=filename or "",
        is_finalized=is_finalized,
        is_sensitive=is_sensitive,
        metadata=metadata,
        source_info=source_info,
    )
    if _extraction_meta:
        _commit_result["extraction_meta"] = _extraction_meta
    return _commit_result


async def resume_completed_hitl_task(
    *,
    config_manager: ConfigManager,
    session_factory: sessionmaker[Session],
    task_id: int,
) -> dict[str, Any]:
    """Resume a pipeline run from a completed HITL task and commit finalized chunks.

    Guards against infinite HITL loops by tracking a resume counter.  If the
    same run has already been resumed ``MAX_HITL_RESUMES`` times, the resume
    is rejected to prevent unbounded re-processing.
    """
    MAX_HITL_RESUMES = 2

    from atlas.hitl_ledger import get_hitl_task

    with session_factory() as session:
        task = get_hitl_task(session, task_id=task_id)
        if task is None:
            raise KeyError("task not found")
        if task.status != "completed":
            raise ValueError("task must be completed to resume")

        run = session.execute(select(WorkflowRun).where(WorkflowRun.id == task.run_id)).scalars().first()
        if run is None:
            raise KeyError("run not found")

        # Guard against double-resume: if the run already completed (i.e. was previously
        # resumed and committed chunks), refuse to re-run to avoid duplicate upserts.
        if run.status == "completed":
            raise ValueError("pipeline run already completed; cannot resume a completed run")

        # Guard against infinite HITL loops: track how many times this run
        # has been resumed.  If it exceeds the limit, refuse.
        m = run.meta or {}
        resume_count = int(m.get("hitl_resume_count", 0))
        if resume_count >= MAX_HITL_RESUMES:
            raise ValueError(
                f"pipeline run has been resumed {resume_count} times "
                f"(max {MAX_HITL_RESUMES}); refusing to resume again to "
                f"prevent infinite HITL loops"
            )

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

        resumed_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        run.status = "running"
        run.current_node = "ingest"
        # Record resume provenance in run metadata.
        m["hitl_task_id"] = int(task_id)
        m["resumed_at"] = resumed_at
        m["hitl_resume_count"] = resume_count + 1
        run.meta = m
        # Record resume timestamp on the HITL task itself (best-effort).
        try:
            tm = task.meta or {}
            tm["resumed_at"] = resumed_at
            task.meta = tm
        except Exception:
            pass
        session.commit()

    # Re-run pipeline + commit using the existing run_id.
    # ``is_hitl_resume=True`` tells routing to skip cleanup-rejudge and
    # refine loops — the human reviewer already approved the content.
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
        is_hitl_resume=True,
    )
