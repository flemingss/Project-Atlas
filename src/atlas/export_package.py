from __future__ import annotations

import datetime as dt
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from qdrant_client.http import models as qm
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from atlas.doc_versions import get_active_doc_version, get_latest_doc_version_from_runs
from atlas.models import ArtifactRef, WorkflowRun
from atlas.settings import Settings
from atlas.vectorstore.qdrant_store import QdrantStore


def build_frontmatter(meta: dict[str, Any]) -> str:
    """Build a YAML frontmatter block from a metadata dict.

    The dict is intentionally open-ended so callers can add custom
    fields (tags, labels, etc.) without changing this function.
    """
    lines: list[str] = ["---"]
    for k, v in meta.items():
        if v is None:
            continue
        # YAML accepts JSON scalars; this avoids adding PyYAML.
        lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


# keep the old private name as an alias for any in-tree callers
_frontmatter_yaml = build_frontmatter


def build_index_md(
    docs: list[dict[str, Any]],
    *,
    exported_at: str | None = None,
    title: str = "Export Inventory",
) -> str:
    """Build an INDEX.md markdown document inventorying exported files.

    Each entry in *docs* should have at least ``doc_id``; other supported
    keys: ``doc_version``, ``workspace``, ``project``, ``collection``,
    ``chunks``, ``file``.
    """
    parts: list[str] = [f"# {title}", ""]
    if exported_at:
        parts.append(f"Exported: {exported_at}  ")
    parts.append(f"Total documents: {len(docs)}")
    parts.append("")
    parts.append("| # | Doc ID | Version | Workspace | Project | Collection | Chunks | File |")
    parts.append("|---|--------|---------|-----------|---------|------------|--------|------|")
    for i, d in enumerate(docs, start=1):
        def _cell(key: str) -> str:
            v = d.get(key)
            return str(v) if v is not None else ""
        row = (
            f"| {i} "
            f"| {_cell('doc_id')} "
            f"| {_cell('doc_version')} "
            f"| {_cell('workspace')} "
            f"| {_cell('project')} "
            f"| {_cell('collection')} "
            f"| {_cell('chunks')} "
            f"| {_cell('file')} |"
        )
        parts.append(row)
    parts.append("")
    return "\n".join(parts)



def _as_point_id(p: Any) -> str:
    if isinstance(p, dict):
        return str(p.get("id", ""))
    return str(getattr(p, "id", ""))


def _as_point_payload(p: Any) -> dict[str, Any]:
    if isinstance(p, dict):
        return dict(p.get("payload") or {})
    payload = getattr(p, "payload", None)
    return dict(payload or {})


def _latest_run_for_version(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    doc_id: str,
    doc_version: str,
) -> WorkflowRun | None:
    stmt = (
        select(WorkflowRun)
        .where(WorkflowRun.tenant_id == tenant_id)
        .where(WorkflowRun.project_id == project_id)
        .where(WorkflowRun.doc_id == doc_id)
        .where(WorkflowRun.doc_version == doc_version)
        .order_by(WorkflowRun.id.desc())
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


async def export_doc_package(
    *,
    session_factory: sessionmaker[Session],
    tenant_id: str,
    project_id: str,
    corpus_id: str | None = None,
    doc_id: str,
    doc_version: str | None = None,
) -> bytes:
    """Build a zip export package for a doc.

    Contents:
      - manifest.json
      - document.md (markdown projection with YAML frontmatter)
      - index.json (chunk_id / chunk_index mapping)
      - artifacts/ (best-effort: copies artifact_refs for the selected run)
    """

    settings = Settings()
    artifacts_dir = Path(settings.atlas_artifacts_dir).resolve()

    with session_factory() as session:
        resolved_version = doc_version
        if resolved_version is None:
            resolved_version = get_active_doc_version(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                doc_id=doc_id,
                corpus_id=corpus_id,
            )
        if resolved_version is None:
            resolved_version = get_latest_doc_version_from_runs(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                doc_id=doc_id,
            )
        if resolved_version is None:
            raise KeyError("doc not found")

        run = _latest_run_for_version(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            doc_id=doc_id,
            doc_version=resolved_version,
        )
        if run is None:
            raise KeyError("no runs for doc_version")

        refs = list(
            session.execute(
                select(ArtifactRef).where(ArtifactRef.run_id == run.id).order_by(ArtifactRef.id.asc())
            ).scalars()
        )

    # Pull markdown projection from artifacts (best-effort).
    markdown_projection = ""
    chunk_manifest = ""
    for r in refs:
        if str(r.kind) == "markdown_projection":
            try:
                p = (artifacts_dir / str(r.path)).resolve()
                markdown_projection = p.read_text(encoding="utf-8")
            except Exception:
                markdown_projection = ""
            break

    # Pull chunk manifest if present.
    for r in refs:
        if str(r.kind) == "chunk_manifest":
            try:
                p = (artifacts_dir / str(r.path)).resolve()
                chunk_manifest = p.read_text(encoding="utf-8")
            except Exception:
                chunk_manifest = ""
            break

    # Build index from Qdrant.
    store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection="atlas_chunks")
    must = [
        qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=tenant_id)),
        qm.FieldCondition(key="project_id", match=qm.MatchValue(value=project_id)),
        qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id)),
        qm.FieldCondition(key="doc_version", match=qm.MatchValue(value=resolved_version)),
        qm.FieldCondition(key="is_finalized", match=qm.MatchValue(value=True)),
    ]
    if corpus_id is not None:
        must.insert(2, qm.FieldCondition(key="corpus_id", match=qm.MatchValue(value=corpus_id)))
    points = await run_in_threadpool(store.scroll_points, must=must, limit=256, max_points=50_000)

    chunks: list[dict[str, Any]] = []
    for p in points:
        payload = _as_point_payload(p)
        chunks.append(
            {
                "id": _as_point_id(p),
                "chunk_index": payload.get("chunk_index"),
                "content_hash": payload.get("content_hash"),
                "text_len": len(str(payload.get("text") or "")),
                "created_at": payload.get("created_at"),
            }
        )
    chunks.sort(key=lambda x: (x.get("chunk_index") is None, int(x.get("chunk_index") or 0)))

    exported_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "exported_at": exported_at,
        "tenant_id": tenant_id,
        "project_id": project_id,
        "corpus_id": corpus_id,
        "doc_id": doc_id,
        "doc_version": resolved_version,
        "run_id": int(run.id),
        "workflow_status": str(run.status),
        "artifacts": [
            {
                "kind": str(r.kind),
                "path": str(r.path),
                "sha256": str(r.sha256),
                "mime_type": str(r.mime_type),
                "node_run_id": (None if r.node_run_id is None else int(r.node_run_id)),
            }
            for r in refs
        ],
        "qdrant": {"collection": store.collection, "chunks": len(chunks)},
    }

    index_config: dict[str, Any] = {
        "schema_version": 1,
        "tenant_id": tenant_id,
        "project_id": project_id,
        "corpus_id": corpus_id,
        "doc_id": doc_id,
        "doc_version": resolved_version,
        "qdrant_collection": store.collection,
        "embedding": {
            # Best-effort: derive from one of the points if present.
            "model": None,
            "provider": None,
            "params": None,
        },
        "metadata_fields": [
            "tenant_id",
            "project_id",
            "corpus_id",
            "doc_id",
            "doc_version",
            "chunk_index",
            "section_path",
            "has_table",
            "is_procedure",
            "has_code",
            "source_mime_type",
            "is_finalized",
            "is_sensitive",
            "is_active_version",
        ],
    }

    # Infer embedding info from first payload if possible.
    if points:
        try:
            payload0 = _as_point_payload(points[0])
            index_config["embedding"] = {
                "model": payload0.get("embedding_model"),
                "provider": payload0.get("embedding_provider"),
                "params": payload0.get("embedding_params"),
            }
        except Exception:
            pass

    frontmatter = build_frontmatter(
        {
            "exported_at": exported_at,
            "tenant_id": tenant_id,
            "project_id": project_id,
            "corpus_id": corpus_id,
            "doc_id": doc_id,
            "doc_version": resolved_version,
            "run_id": int(run.id),
        }
    )
    enriched_markdown = frontmatter + (markdown_projection or "")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
        z.writestr("index.json", json.dumps({"chunks": chunks}, ensure_ascii=False, indent=2, sort_keys=True))
        z.writestr("document.md", enriched_markdown)
        z.writestr("index_config.json", json.dumps(index_config, ensure_ascii=False, indent=2, sort_keys=True))
        if chunk_manifest.strip():
            z.writestr("chunk_manifest.jsonl", chunk_manifest)

        # Include artifacts as stored on disk (best-effort).
        for r in refs:
            try:
                src = (artifacts_dir / str(r.path)).resolve()
                if not src.exists() or not src.is_file():
                    continue
                z.write(src, arcname=str(r.path))
            except Exception:
                continue

    return buf.getvalue()


async def get_doc_markdown(
    *,
    session_factory: sessionmaker[Session],
    tenant_id: str,
    project_id: str,
    corpus_id: str | None = None,
    doc_id: str,
    doc_version: str | None = None,
) -> str:
    """Return the plain markdown text for a document (no YAML frontmatter).

    Tries the stored ``markdown_projection`` artifact first; falls back to
    reconstructing from ordered Qdrant chunk texts so the export is never empty.
    """
    settings = Settings()
    artifacts_dir = Path(settings.atlas_artifacts_dir).resolve()

    with session_factory() as session:
        resolved_version = doc_version
        if resolved_version is None:
            resolved_version = get_active_doc_version(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                doc_id=doc_id,
                corpus_id=corpus_id,
            )
        if resolved_version is None:
            resolved_version = get_latest_doc_version_from_runs(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                doc_id=doc_id,
            )
        if resolved_version is None:
            raise KeyError("doc not found")

        run = _latest_run_for_version(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            doc_id=doc_id,
            doc_version=resolved_version,
        )
        refs = []
        if run is not None:
            from sqlalchemy import select as _select

            refs = list(
                session.execute(
                    _select(ArtifactRef)
                    .where(ArtifactRef.run_id == run.id)
                    .order_by(ArtifactRef.id.asc())
                ).scalars()
            )

    # Try the stored markdown projection from artifacts first.
    markdown_projection = ""
    for r in refs:
        if str(r.kind) == "markdown_projection":
            try:
                p = (artifacts_dir / str(r.path)).resolve()
                markdown_projection = p.read_text(encoding="utf-8")
            except Exception:
                markdown_projection = ""
            break

    if markdown_projection.strip():
        # Strip Atlas YAML frontmatter so callers get clean content.
        text = markdown_projection
        if text.startswith("---\n"):
            end = text.find("\n---\n", 4)
            if end != -1:
                text = text[end + len("\n---\n"):]
        return text.strip()

    # Fallback: reconstruct from Qdrant chunk texts in order.
    store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection="atlas_chunks")
    must = [
        qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=tenant_id)),
        qm.FieldCondition(key="project_id", match=qm.MatchValue(value=project_id)),
        qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id)),
        qm.FieldCondition(key="doc_version", match=qm.MatchValue(value=resolved_version)),
    ]
    if corpus_id is not None:
        must.insert(2, qm.FieldCondition(key="corpus_id", match=qm.MatchValue(value=corpus_id)))
    points = await run_in_threadpool(store.scroll_points, must=must, limit=256, max_points=50_000)

    ordered = sorted(
        points,
        key=lambda p: (
            _as_point_payload(p).get("chunk_index") is None,
            int(_as_point_payload(p).get("chunk_index") or 0),
        ),
    )
    return "\n\n".join(
        str(_as_point_payload(p).get("text") or "").strip()
        for p in ordered
        if (_as_point_payload(p).get("text") or "").strip()
    )


async def export_doc_lean(
    *,
    session_factory: sessionmaker[Session],
    tenant_id: str,
    project_id: str,
    corpus_id: str | None = None,
    doc_id: str,
    doc_version: str | None = None,
) -> bytes:
    """Lean export: a ZIP containing ``document.md`` with YAML frontmatter.

    The resulting ZIP is suitable for dropping directly into another RAG
    pipeline — no manifest, no artifacts, no index JSON.  Frontmatter
    carries scope metadata so downstream systems can infer provenance.
    """
    content = await get_doc_markdown(
        session_factory=session_factory,
        tenant_id=tenant_id,
        project_id=project_id,
        corpus_id=corpus_id,
        doc_id=doc_id,
        doc_version=doc_version,
    )

    exported_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    frontmatter = build_frontmatter(
        {
            "exported_at": exported_at,
            "tenant_id": tenant_id,
            "project_id": project_id,
            "corpus_id": corpus_id,
            "doc_id": doc_id,
            "doc_version": doc_version,
        }
    )
    enriched = frontmatter + (content or "")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("document.md", enriched)
    return buf.getvalue()
