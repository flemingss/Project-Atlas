from __future__ import annotations

import io
import json
import zipfile
from typing import Any

from qdrant_client.http import models as qm
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from atlas.config_manager import ConfigManager
from atlas.export_package import export_doc_package, get_doc_markdown, build_frontmatter, build_index_md
from atlas.pipeline.runner import ingest_text_via_pipeline
from atlas.settings import Settings
from atlas.vectorstore.qdrant_store import QdrantStore


def _safe_filename(value: str) -> str:
    v = (value or "").strip()
    if v == "":
        return "empty"
    # Keep it simple and cross-platform: remove path separators.
    return v.replace("/", "_").replace("\\", "_").replace(":", "_")


def _strip_yaml_frontmatter(md: str) -> str:
    text = md or ""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end == -1:
        return text
    return text[end + len("\n---\n") :]


async def export_corpus_package(
    *,
    session_factory: sessionmaker[Session],
    tenant_id: str,
    project_id: str,
    corpus_id: str,
    max_docs: int = 200,
) -> bytes:
    """Export a corpus-level ZIP containing per-document export ZIPs.

    The corpus is defined by (tenant_id, project_id, corpus_id).

    Output ZIP:
      - corpus_manifest.json
      - INDEX.md (inventory of all docs in the export)
      - docs/<doc_id>_v<doc_version>.zip (doc export packages)
    """

    settings = Settings()
    store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection="atlas_chunks")

    must = [
        qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=tenant_id)),
        qm.FieldCondition(key="project_id", match=qm.MatchValue(value=project_id)),
        qm.FieldCondition(key="corpus_id", match=qm.MatchValue(value=corpus_id)),
        qm.FieldCondition(key="is_finalized", match=qm.MatchValue(value=True)),
        qm.FieldCondition(key="is_active_version", match=qm.MatchValue(value=True)),
    ]

    # Scroll all points for this corpus, then dedupe by doc_id.
    points = await run_in_threadpool(store.scroll_points, must=must, limit=256, max_points=200_000)

    seen: set[tuple[str, str]] = set()
    chunk_counts: dict[tuple[str, str], int] = {}
    docs: list[dict[str, str]] = []
    for p in points:
        payload = p.get("payload") if isinstance(p, dict) else getattr(p, "payload", {})
        payload = payload or {}
        doc_id = str(payload.get("doc_id") or "").strip()
        doc_version = str(payload.get("doc_version") or "").strip()
        if not doc_id or not doc_version:
            continue
        key = (doc_id, doc_version)
        chunk_counts[key] = chunk_counts.get(key, 0) + 1
        if key in seen:
            continue
        seen.add(key)
        docs.append({"doc_id": doc_id, "doc_version": doc_version})
        if len(docs) >= int(max_docs):
            break

    import datetime as _dt
    exported_at = _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "corpus_manifest.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "corpus_id": corpus_id,
                    "doc_count": len(docs),
                    "docs": docs,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
        )

        index_entries: list[dict[str, Any]] = []
        for d in docs:
            doc_id = d["doc_id"]
            doc_version = d["doc_version"]
            blob = await export_doc_package(
                session_factory=session_factory,
                tenant_id=tenant_id,
                project_id=project_id,
                corpus_id=corpus_id,
                doc_id=doc_id,
                doc_version=doc_version,
            )
            arcname = f"docs/{_safe_filename(doc_id)}_v{_safe_filename(doc_version)}.zip"
            z.writestr(arcname, blob)
            index_entries.append({
                "doc_id": doc_id,
                "doc_version": doc_version,
                "workspace": tenant_id,
                "project": project_id,
                "collection": corpus_id,
                "chunks": chunk_counts.get((doc_id, doc_version), 0),
                "file": arcname,
            })

        z.writestr("INDEX.md", build_index_md(index_entries, exported_at=exported_at))

    return buf.getvalue()


async def export_corpus_lean(
    *,
    session_factory: sessionmaker[Session],
    tenant_id: str,
    project_id: str,
    corpus_id: str,
    max_docs: int = 200,
) -> bytes:
    """Lean corpus export: a flat ZIP of ``docs/{doc_id}.md`` files.

    Each file contains markdown with YAML frontmatter carrying scope
    metadata.  An ``INDEX.md`` inventory file is included at the root.
    """
    settings = Settings()
    store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection="atlas_chunks")

    must = [
        qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=tenant_id)),
        qm.FieldCondition(key="project_id", match=qm.MatchValue(value=project_id)),
        qm.FieldCondition(key="corpus_id", match=qm.MatchValue(value=corpus_id)),
        qm.FieldCondition(key="is_finalized", match=qm.MatchValue(value=True)),
        qm.FieldCondition(key="is_active_version", match=qm.MatchValue(value=True)),
    ]

    points = await run_in_threadpool(store.scroll_points, must=must, limit=256, max_points=200_000)

    seen: set[tuple[str, str]] = set()
    chunk_counts: dict[tuple[str, str], int] = {}
    docs: list[dict[str, str]] = []
    for p in points:
        payload = p.get("payload") if isinstance(p, dict) else getattr(p, "payload", {})
        payload = payload or {}
        doc_id = str(payload.get("doc_id") or "").strip()
        doc_version = str(payload.get("doc_version") or "").strip()
        if not doc_id or not doc_version:
            continue
        key = (doc_id, doc_version)
        chunk_counts[key] = chunk_counts.get(key, 0) + 1
        if key in seen:
            continue
        seen.add(key)
        docs.append({"doc_id": doc_id, "doc_version": doc_version})
        if len(docs) >= int(max_docs):
            break

    import datetime as _dt
    exported_at = _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "corpus_manifest.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "export_format": "lean",
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "corpus_id": corpus_id,
                    "doc_count": len(docs),
                    "docs": [{"doc_id": d["doc_id"], "doc_version": d["doc_version"]} for d in docs],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
        )

        index_entries: list[dict[str, Any]] = []
        for d in docs:
            doc_id = d["doc_id"]
            doc_version = d["doc_version"]
            try:
                content = await get_doc_markdown(
                    session_factory=session_factory,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    corpus_id=corpus_id,
                    doc_id=doc_id,
                    doc_version=doc_version,
                )
            except Exception:  # noqa: BLE001
                content = ""
            arcname = f"{_safe_filename(doc_id)}.md"
            # Add YAML frontmatter to each MD file
            fm = build_frontmatter({
                "exported_at": exported_at,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "corpus_id": corpus_id,
                "doc_id": doc_id,
                "doc_version": doc_version,
            })
            z.writestr(arcname, fm + (content or ""))
            index_entries.append({
                "doc_id": doc_id,
                "doc_version": doc_version,
                "workspace": tenant_id,
                "project": project_id,
                "collection": corpus_id,
                "chunks": chunk_counts.get((doc_id, doc_version), 0),
                "file": arcname,
            })

        z.writestr("INDEX.md", build_index_md(index_entries, exported_at=exported_at))

    return buf.getvalue()


async def export_project_package(
    *,
    session_factory: sessionmaker[Session],
    tenant_id: str,
    project_id: str,
    max_docs: int = 2000,
) -> bytes:
    """Export a project-level ZIP containing per-corpus export ZIPs."""
    settings = Settings()
    store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection="atlas_chunks")

    must = [
        qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=tenant_id)),
        qm.FieldCondition(key="project_id", match=qm.MatchValue(value=project_id)),
        qm.FieldCondition(key="is_finalized", match=qm.MatchValue(value=True)),
        qm.FieldCondition(key="is_active_version", match=qm.MatchValue(value=True)),
    ]
    points = await run_in_threadpool(store.scroll_points, must=must, limit=256, max_points=200_000)

    corpora: set[str] = set()
    for p in points:
        payload = p.get("payload") if isinstance(p, dict) else getattr(p, "payload", {})
        payload = payload or {}
        cid = str(payload.get("corpus_id") or "").strip()
        if cid:
            corpora.add(cid)

    ordered_corpora = sorted(corpora)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "project_manifest.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "corpora": ordered_corpora,
                    "corpora_count": len(ordered_corpora),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
        )

        import datetime as _dt
        _exported_at = _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
        _index_entries: list[dict[str, Any]] = []
        for cid in ordered_corpora:
            blob = await export_corpus_package(
                session_factory=session_factory,
                tenant_id=tenant_id,
                project_id=project_id,
                corpus_id=cid,
                max_docs=int(max_docs),
            )
            arcname = f"corpora/{_safe_filename(cid)}.zip"
            z.writestr(arcname, blob)
            _index_entries.append({
                "doc_id": cid,
                "doc_version": "",
                "workspace": tenant_id,
                "project": project_id,
                "collection": cid,
                "chunks": "",
                "file": arcname,
            })
        z.writestr("INDEX.md", build_index_md(_index_entries, exported_at=_exported_at, title="Project Export Inventory"))

    return buf.getvalue()


async def export_project_lean(
    *,
    session_factory: sessionmaker[Session],
    tenant_id: str,
    project_id: str,
    max_docs: int = 2000,
) -> bytes:
    """Lean project export containing per-corpus lean ZIPs."""
    settings = Settings()
    store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection="atlas_chunks")

    must = [
        qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=tenant_id)),
        qm.FieldCondition(key="project_id", match=qm.MatchValue(value=project_id)),
        qm.FieldCondition(key="is_finalized", match=qm.MatchValue(value=True)),
        qm.FieldCondition(key="is_active_version", match=qm.MatchValue(value=True)),
    ]
    points = await run_in_threadpool(store.scroll_points, must=must, limit=256, max_points=200_000)

    corpora: set[str] = set()
    for p in points:
        payload = p.get("payload") if isinstance(p, dict) else getattr(p, "payload", {})
        payload = payload or {}
        cid = str(payload.get("corpus_id") or "").strip()
        if cid:
            corpora.add(cid)

    ordered_corpora = sorted(corpora)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "project_manifest.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "export_format": "lean",
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "corpora": ordered_corpora,
                    "corpora_count": len(ordered_corpora),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
        )

        import datetime as _dt
        _exported_at = _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
        _index_entries: list[dict[str, Any]] = []
        for cid in ordered_corpora:
            blob = await export_corpus_lean(
                session_factory=session_factory,
                tenant_id=tenant_id,
                project_id=project_id,
                corpus_id=cid,
                max_docs=int(max_docs),
            )
            arcname = f"corpora/{_safe_filename(cid)}.zip"
            z.writestr(arcname, blob)
            _index_entries.append({
                "doc_id": cid,
                "doc_version": "",
                "workspace": tenant_id,
                "project": project_id,
                "collection": cid,
                "chunks": "",
                "file": arcname,
            })
        z.writestr("INDEX.md", build_index_md(_index_entries, exported_at=_exported_at, title="Project Lean Export Inventory"))

    return buf.getvalue()


async def export_tenant_package(
    *,
    session_factory: sessionmaker[Session],
    tenant_id: str,
    max_docs: int = 2000,
) -> bytes:
    """Export a tenant-level ZIP containing project/corpus export ZIPs."""
    settings = Settings()
    store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection="atlas_chunks")

    must = [
        qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=tenant_id)),
        qm.FieldCondition(key="is_finalized", match=qm.MatchValue(value=True)),
        qm.FieldCondition(key="is_active_version", match=qm.MatchValue(value=True)),
    ]
    points = await run_in_threadpool(store.scroll_points, must=must, limit=256, max_points=250_000)

    projects: dict[str, set[str]] = {}
    for p in points:
        payload = p.get("payload") if isinstance(p, dict) else getattr(p, "payload", {})
        payload = payload or {}
        pid = str(payload.get("project_id") or "").strip()
        cid = str(payload.get("corpus_id") or "").strip()
        if not pid or not cid:
            continue
        projects.setdefault(pid, set()).add(cid)

    ordered_projects = sorted(projects.keys())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "tenant_manifest.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "tenant_id": tenant_id,
                    "projects": [
                        {"project_id": p, "corpora": sorted(projects[p])}
                        for p in ordered_projects
                    ],
                    "projects_count": len(ordered_projects),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
        )

        import datetime as _dt
        _exported_at = _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
        _index_entries: list[dict[str, Any]] = []
        for pid in ordered_projects:
            for cid in sorted(projects[pid]):
                blob = await export_corpus_package(
                    session_factory=session_factory,
                    tenant_id=tenant_id,
                    project_id=pid,
                    corpus_id=cid,
                    max_docs=int(max_docs),
                )
                arcname = f"projects/{_safe_filename(pid)}/corpora/{_safe_filename(cid)}.zip"
                z.writestr(arcname, blob)
                _index_entries.append({
                    "doc_id": cid,
                    "doc_version": "",
                    "workspace": tenant_id,
                    "project": pid,
                    "collection": cid,
                    "chunks": "",
                    "file": arcname,
                })
        z.writestr("INDEX.md", build_index_md(_index_entries, exported_at=_exported_at, title="Tenant Export Inventory"))

    return buf.getvalue()


async def export_tenant_lean(
    *,
    session_factory: sessionmaker[Session],
    tenant_id: str,
    max_docs: int = 2000,
) -> bytes:
    """Lean tenant export containing project/corpus lean ZIPs."""
    settings = Settings()
    store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection="atlas_chunks")

    must = [
        qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=tenant_id)),
        qm.FieldCondition(key="is_finalized", match=qm.MatchValue(value=True)),
        qm.FieldCondition(key="is_active_version", match=qm.MatchValue(value=True)),
    ]
    points = await run_in_threadpool(store.scroll_points, must=must, limit=256, max_points=250_000)

    projects: dict[str, set[str]] = {}
    for p in points:
        payload = p.get("payload") if isinstance(p, dict) else getattr(p, "payload", {})
        payload = payload or {}
        pid = str(payload.get("project_id") or "").strip()
        cid = str(payload.get("corpus_id") or "").strip()
        if not pid or not cid:
            continue
        projects.setdefault(pid, set()).add(cid)

    ordered_projects = sorted(projects.keys())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "tenant_manifest.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "export_format": "lean",
                    "tenant_id": tenant_id,
                    "projects": [
                        {"project_id": p, "corpora": sorted(projects[p])}
                        for p in ordered_projects
                    ],
                    "projects_count": len(ordered_projects),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
        )

        import datetime as _dt
        _exported_at = _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
        _index_entries: list[dict[str, Any]] = []
        for pid in ordered_projects:
            for cid in sorted(projects[pid]):
                blob = await export_corpus_lean(
                    session_factory=session_factory,
                    tenant_id=tenant_id,
                    project_id=pid,
                    corpus_id=cid,
                    max_docs=int(max_docs),
                )
                arcname = f"projects/{_safe_filename(pid)}/corpora/{_safe_filename(cid)}.zip"
                z.writestr(arcname, blob)
                _index_entries.append({
                    "doc_id": cid,
                    "doc_version": "",
                    "workspace": tenant_id,
                    "project": pid,
                    "collection": cid,
                    "chunks": "",
                    "file": arcname,
                })
        z.writestr("INDEX.md", build_index_md(_index_entries, exported_at=_exported_at, title="Tenant Lean Export Inventory"))

    return buf.getvalue()


async def import_corpus_package(
    *,
    config_manager: ConfigManager,
    session_factory: sessionmaker[Session],
    tenant_id: str,
    project_id: str,
    corpus_id: str,
    zip_bytes: bytes,
    is_finalized: bool = True,
    is_sensitive: bool = True,
) -> dict[str, Any]:
    """Import a corpus ZIP produced by export_corpus_package.

    Import behavior:
      - Reads each doc export ZIP under docs/*.zip
      - Extracts document.md and re-ingests via the pipeline (re-embeds)

    This keeps the import deterministic and avoids tight coupling to Qdrant internals.
    """

    imported: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes), mode="r") as z:
        names = [n for n in z.namelist() if n.startswith("docs/") and n.lower().endswith(".zip")]
        for name in names:
            try:
                doc_zip_bytes = z.read(name)
            except Exception as e:  # noqa: BLE001
                errors.append({"file": name, "error": f"read_failed: {e}"})
                continue

            try:
                with zipfile.ZipFile(io.BytesIO(doc_zip_bytes), mode="r") as dz:
                    manifest_raw = dz.read("manifest.json").decode("utf-8")
                    manifest = json.loads(manifest_raw)
                    doc_id = str(manifest.get("doc_id") or "").strip()
                    doc_version = str(manifest.get("doc_version") or "").strip()
                    md = dz.read("document.md").decode("utf-8")

                if not doc_id or not doc_version:
                    raise ValueError("manifest missing doc_id/doc_version")

                md = _strip_yaml_frontmatter(md)

                result = await ingest_text_via_pipeline(
                    config_manager=config_manager,
                    session_factory=session_factory,
                    doc_id=doc_id,
                    doc_version=doc_version,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    corpus_id=corpus_id,
                    text=md,
                    source_mime_type="text/markdown",
                    is_finalized=bool(is_finalized),
                    is_sensitive=bool(is_sensitive),
                    metadata={"imported_from": "corpus_package", "source": name},
                )
                imported.append(
                    {
                        "doc_id": doc_id,
                        "doc_version": doc_version,
                        "ok": bool(result.get("ok")),
                        "chunks_upserted": int(result.get("chunks_upserted", 0)),
                    }
                )
            except Exception as e:  # noqa: BLE001
                errors.append({"file": name, "error": repr(e)})

    return {
        "ok": len(errors) == 0,
        "tenant_id": tenant_id,
        "project_id": project_id,
        "corpus_id": corpus_id,
        "docs_imported": len(imported),
        "docs_failed": len(errors),
        "imported": imported,
        "errors": errors,
    }
