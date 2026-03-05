"""Maintenance routes: orphan cleanup, scope reassociation, document operations."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from qdrant_client.http import models as qm
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from atlas.admin._helpers import clean_scope_id, qdrant_collection
from atlas.models import ActiveDocVersion, HitlTaskRow, WorkflowRun
from atlas.settings import Settings
from atlas.vectorstore.qdrant_store import QdrantStore as _DefaultQdrantStore
from atlas.workflow_ledger import (
    WorkflowRunCreateRequest,
    create_workflow_run,
    get_workflow_run,
)


class SetActiveDocVersionRequest(BaseModel):
    doc_version: str
    tenant_id: str | None = None
    project_id: str | None = None
    corpus_id: str | None = None


class ReassociateRunScopeRequest(BaseModel):
    tenant_id: str
    project_id: str
    corpus_id: str


class CleanupOrphansRequest(BaseModel):
    dry_run: bool = True
    max_points: int = 10000
    tenant_id: str | None = None
    project_id: str | None = None
    corpus_id: str | None = None


class AdoptOrphanGroupRequest(BaseModel):
    """Move orphaned Qdrant chunks to a valid scope and register a synthetic WorkflowRun."""
    old_tenant_id: str
    old_project_id: str
    old_doc_id: str
    old_doc_version: str
    tenant_id: str
    project_id: str
    corpus_id: str


def register_maintenance_routes(
    router: APIRouter,
    *,
    session_factory: sessionmaker[Session],
    settings: Settings,
    QdrantStore: type = _DefaultQdrantStore,
) -> None:

    @router.get("/docs/{doc_id}/active-version")
    def doc_active_version(
        doc_id: str,
        tenant_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
        corpus_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        from atlas.doc_versions import get_active_doc_version, get_latest_doc_version_from_runs

        t_id = tenant_id or settings.atlas_default_tenant_id
        p_id = project_id or settings.atlas_default_project_id
        c_id = corpus_id or settings.atlas_default_corpus_id

        with session_factory() as session:
            active = get_active_doc_version(
                session,
                tenant_id=t_id,
                project_id=p_id,
                doc_id=doc_id,
                corpus_id=c_id,
            )
            latest = get_latest_doc_version_from_runs(session, tenant_id=t_id, project_id=p_id, doc_id=doc_id)

        return {
            "tenant_id": t_id,
            "project_id": p_id,
            "corpus_id": c_id,
            "doc_id": doc_id,
            "active_doc_version": active,
            "latest_doc_version": latest,
        }

    @router.post("/docs/{doc_id}/active-version")
    async def set_doc_active_version(doc_id: str, req: SetActiveDocVersionRequest) -> dict[str, Any]:
        from atlas.doc_versions import set_active_doc_version

        t_id = req.tenant_id or settings.atlas_default_tenant_id
        p_id = req.project_id or settings.atlas_default_project_id
        c_id = req.corpus_id or settings.atlas_default_corpus_id
        version = str(req.doc_version)

        with session_factory() as session:
            row = set_active_doc_version(
                session,
                tenant_id=t_id,
                project_id=p_id,
                doc_id=doc_id,
                doc_version=version,
                corpus_id=c_id,
            )

        # Best-effort: update Qdrant payload flags for global search filtering.
        store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection=qdrant_collection())
        base_must = [
            qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=t_id)),
            qm.FieldCondition(key="project_id", match=qm.MatchValue(value=p_id)),
            qm.FieldCondition(key="corpus_id", match=qm.MatchValue(value=c_id)),
            qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id)),
        ]
        version_must = [
            *base_must,
            qm.FieldCondition(key="doc_version", match=qm.MatchValue(value=version)),
        ]
        try:
            await run_in_threadpool(store.set_payload, payload={"is_active_version": False}, must=base_must)
            await run_in_threadpool(store.set_payload, payload={"is_active_version": True}, must=version_must)
        except Exception:
            pass

        return {
            "ok": True,
            "tenant_id": t_id,
            "project_id": p_id,
            "corpus_id": c_id,
            "doc_id": doc_id,
            "active_doc_version": str(row.active_doc_version),
        }

    @router.post("/runs/{run_id}/reassociate-scope")
    async def reassociate_run_scope(run_id: int, req: ReassociateRunScopeRequest) -> dict[str, Any]:
        """Re-associate a run (and its indexed chunks) to a new scope.

        Updates:
        - workflow_runs.tenant_id / project_id / meta.corpus_id
        - hitl_tasks scope columns for the run
        - active_doc_versions row for this doc scope (best-effort move)
        - Qdrant payload scope fields for matching chunks
        """
        target_tenant = clean_scope_id("tenant_id", req.tenant_id)
        target_project = clean_scope_id("project_id", req.project_id)
        target_corpus = clean_scope_id("corpus_id", req.corpus_id)

        with session_factory() as session:
            run = get_workflow_run(session, run_id=run_id)
            if run is None:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

            old_tenant = str(run.tenant_id)
            old_project = str(run.project_id)
            old_doc_id = str(run.doc_id)
            old_doc_version = str(run.doc_version)
            old_meta = dict(run.meta or {})
            old_corpus = str(old_meta.get("corpus_id") or settings.atlas_default_corpus_id)

            run.tenant_id = target_tenant
            run.project_id = target_project
            next_meta = dict(old_meta)
            next_meta["corpus_id"] = target_corpus
            run.meta = next_meta

            hitl_rows = list(
                session.query(HitlTaskRow)
                .filter(HitlTaskRow.run_id == run_id)
                .all()
            )
            for row in hitl_rows:
                row.tenant_id = target_tenant
                row.project_id = target_project

            # Best-effort: move ActiveDocVersion row to the new scope.
            old_adv = (
                session.query(ActiveDocVersion)
                .filter(
                    ActiveDocVersion.tenant_id == old_tenant,
                    ActiveDocVersion.project_id == old_project,
                    ActiveDocVersion.doc_id == old_doc_id,
                )
                .first()
            )
            moved_active_version = None
            if old_adv is not None:
                existing_new = (
                    session.query(ActiveDocVersion)
                    .filter(
                        ActiveDocVersion.tenant_id == target_tenant,
                        ActiveDocVersion.project_id == target_project,
                        ActiveDocVersion.doc_id == old_doc_id,
                    )
                    .first()
                )
                if existing_new is None:
                    existing_new = ActiveDocVersion(
                        tenant_id=target_tenant,
                        project_id=target_project,
                        doc_id=old_doc_id,
                        active_doc_version=old_adv.active_doc_version,
                    )
                    session.add(existing_new)
                moved_active_version = str(old_adv.active_doc_version)
                session.delete(old_adv)

            session.commit()

        # Update Qdrant payload scope for this doc+version in old scope.
        store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection=qdrant_collection())
        qdrant_updated = True
        qdrant_error = ""
        try:
            must = [
                qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=old_tenant)),
                qm.FieldCondition(key="project_id", match=qm.MatchValue(value=old_project)),
                qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=old_doc_id)),
                qm.FieldCondition(key="doc_version", match=qm.MatchValue(value=old_doc_version)),
            ]
            await run_in_threadpool(
                store.set_payload,
                payload={
                    "tenant_id": target_tenant,
                    "project_id": target_project,
                    "corpus_id": target_corpus,
                },
                must=must,
            )
        except Exception as exc:  # noqa: BLE001
            qdrant_updated = False
            qdrant_error = str(exc)

        return {
            "ok": True,
            "run_id": int(run_id),
            "doc_id": old_doc_id,
            "doc_version": old_doc_version,
            "from": {
                "tenant_id": old_tenant,
                "project_id": old_project,
                "corpus_id": old_corpus,
            },
            "to": {
                "tenant_id": target_tenant,
                "project_id": target_project,
                "corpus_id": target_corpus,
            },
            "hitl_rows_updated": len(hitl_rows),
            "active_doc_version_moved": moved_active_version,
            "qdrant_payload_updated": qdrant_updated,
            "qdrant_error": qdrant_error,
        }

    @router.post("/maintenance/cleanup-orphan-chunks")
    async def cleanup_orphan_chunks(req: CleanupOrphansRequest) -> dict[str, Any]:
        """Find and optionally delete Qdrant points with no matching WorkflowRun scope.

        A point is considered orphaned when (tenant_id, project_id, doc_id, doc_version)
        does not exist in workflow_runs.
        """
        max_points = max(1, min(int(req.max_points), 200000))

        with session_factory() as session:
            stmt = select(
                WorkflowRun.tenant_id,
                WorkflowRun.project_id,
                WorkflowRun.doc_id,
                WorkflowRun.doc_version,
            )
            if req.tenant_id:
                stmt = stmt.where(WorkflowRun.tenant_id == req.tenant_id)
            if req.project_id:
                stmt = stmt.where(WorkflowRun.project_id == req.project_id)
            run_rows = session.execute(stmt).all()

        valid_keys = {
            (str(t), str(p), str(d), str(v))
            for t, p, d, v in run_rows
        }

        collection = qdrant_collection()
        scanned = 0
        orphan_groups: dict[tuple[str, str, str, str], int] = {}

        scope_must_qm: list[qm.FieldCondition] = []
        if req.tenant_id:
            scope_must_qm.append(qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=req.tenant_id)))
        if req.project_id:
            scope_must_qm.append(qm.FieldCondition(key="project_id", match=qm.MatchValue(value=req.project_id)))
        if req.corpus_id:
            scope_must_qm.append(qm.FieldCondition(key="corpus_id", match=qm.MatchValue(value=req.corpus_id)))

        store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection=collection)
        try:
            points = await run_in_threadpool(
                store.scroll_points,
                must=scope_must_qm,
                limit=500,
                max_points=max_points,
            )
        except Exception:
            # Collection doesn't exist yet — nothing to scan
            points = []
        scanned = len(points)

        for point in points:
            payload = {}
            if isinstance(point, dict):
                payload = point.get("payload") or {}
            else:
                payload = getattr(point, "payload", {}) or {}

            tenant_id = payload.get("tenant_id")
            project_id = payload.get("project_id")
            doc_id = payload.get("doc_id")
            doc_version = payload.get("doc_version")
            if not tenant_id or not project_id or not doc_id or not doc_version:
                continue

            key = (str(tenant_id), str(project_id), str(doc_id), str(doc_version))
            if key in valid_keys:
                continue
            orphan_groups[key] = orphan_groups.get(key, 0) + 1

        deleted_groups = 0
        if not req.dry_run and orphan_groups:
            for tenant_id, project_id, doc_id, doc_version in orphan_groups.keys():
                must = [
                    qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=tenant_id)),
                    qm.FieldCondition(key="project_id", match=qm.MatchValue(value=project_id)),
                    qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id)),
                    qm.FieldCondition(key="doc_version", match=qm.MatchValue(value=doc_version)),
                ]
                if req.corpus_id:
                    must.append(qm.FieldCondition(key="corpus_id", match=qm.MatchValue(value=req.corpus_id)))
                await run_in_threadpool(store.delete_by_filter, must=must)
                deleted_groups += 1

        orphan_points = sum(orphan_groups.values())

        # ── Reverse direction: DB runs with no Qdrant chunks (dangling runs) ──
        qdrant_keys: set[tuple[str, str, str, str]] = set()
        for point in points:
            payload = {}
            if isinstance(point, dict):
                payload = point.get("payload") or {}
            else:
                payload = getattr(point, "payload", {}) or {}
            t = payload.get("tenant_id")
            p = payload.get("project_id")
            d = payload.get("doc_id")
            v = payload.get("doc_version")
            if t and p and d and v:
                qdrant_keys.add((str(t), str(p), str(d), str(v)))

        dangling_runs_list: list[dict[str, Any]] = []
        with session_factory() as session:
            stmt = select(WorkflowRun)
            if req.tenant_id:
                stmt = stmt.where(WorkflowRun.tenant_id == req.tenant_id)
            if req.project_id:
                stmt = stmt.where(WorkflowRun.project_id == req.project_id)
            stmt = stmt.order_by(WorkflowRun.id.desc()).limit(200)
            all_runs = session.execute(stmt).scalars().all()
            for run in all_runs:
                rkey = (str(run.tenant_id), str(run.project_id), str(run.doc_id), str(run.doc_version))
                if rkey not in qdrant_keys:
                    dangling_runs_list.append({
                        "run_id": int(run.id),
                        "tenant_id": str(run.tenant_id),
                        "project_id": str(run.project_id),
                        "doc_id": str(run.doc_id),
                        "doc_version": str(run.doc_version),
                        "status": str(run.status),
                        "current_node": str(run.current_node),
                        "corpus_id": (run.meta or {}).get("corpus_id", ""),
                        "created_at": run.created_at.isoformat() if run.created_at else None,
                    })
                if len(dangling_runs_list) >= 50:
                    break

        return {
            "ok": True,
            "dry_run": bool(req.dry_run),
            "scanned_points": int(scanned),
            "max_points": int(max_points),
            "orphan_groups": int(len(orphan_groups)),
            "orphan_points_estimated": int(orphan_points),
            "deleted_groups": int(deleted_groups),
            "sample_orphans": [
                {
                    "tenant_id": k[0],
                    "project_id": k[1],
                    "doc_id": k[2],
                    "doc_version": k[3],
                    "points": v,
                }
                for k, v in list(orphan_groups.items())[:20]
            ],
            "dangling_runs": dangling_runs_list,
        }

    @router.post("/maintenance/adopt-orphan-group")
    async def adopt_orphan_group(req: AdoptOrphanGroupRequest) -> dict[str, Any]:
        """Adopt orphaned Qdrant chunks by moving them to a valid scope.

        Creates a synthetic WorkflowRun so the chunks are no longer orphaned,
        then updates the Qdrant payload scope fields.
        """
        target_tenant = clean_scope_id("tenant_id", req.tenant_id)
        target_project = clean_scope_id("project_id", req.project_id)
        target_corpus = clean_scope_id("corpus_id", req.corpus_id)
        old_tenant = req.old_tenant_id.strip()
        old_project = req.old_project_id.strip()
        old_doc_id = req.old_doc_id.strip()
        old_doc_version = req.old_doc_version.strip()

        if not old_tenant or not old_project or not old_doc_id or not old_doc_version:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="All old scope fields must be non-empty")

        # Count existing Qdrant points in the old scope.
        store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection=qdrant_collection())
        old_must = [
            qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=old_tenant)),
            qm.FieldCondition(key="project_id", match=qm.MatchValue(value=old_project)),
            qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=old_doc_id)),
            qm.FieldCondition(key="doc_version", match=qm.MatchValue(value=old_doc_version)),
        ]
        old_points = await run_in_threadpool(
            store.scroll_points, must=old_must, limit=1, max_points=1,
        )
        if not old_points:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=404,
                detail=f"No Qdrant points found for ({old_tenant}/{old_project}/{old_doc_id}/{old_doc_version})",
            )

        # Create a synthetic WorkflowRun so this group is no longer orphaned.
        with session_factory() as session:
            wf_run = create_workflow_run(
                session,
                req=WorkflowRunCreateRequest(
                    tenant_id=target_tenant,
                    project_id=target_project,
                    doc_id=old_doc_id,
                    doc_version=old_doc_version,
                    status="complete",
                    current_node="adopted",
                    meta={
                        "source": "orphan_adoption",
                        "corpus_id": target_corpus,
                        "adopted_from": {
                            "tenant_id": old_tenant,
                            "project_id": old_project,
                        },
                    },
                ),
            )
            session.commit()
            run_id = int(wf_run.id)

        # Update Qdrant payload scope.
        qdrant_updated = True
        qdrant_error = ""
        try:
            await run_in_threadpool(
                store.set_payload,
                payload={
                    "tenant_id": target_tenant,
                    "project_id": target_project,
                    "corpus_id": target_corpus,
                },
                must=old_must,
            )
        except Exception as exc:  # noqa: BLE001
            qdrant_updated = False
            qdrant_error = str(exc)

        return {
            "ok": True,
            "run_id": run_id,
            "doc_id": old_doc_id,
            "doc_version": old_doc_version,
            "from": {
                "tenant_id": old_tenant,
                "project_id": old_project,
            },
            "to": {
                "tenant_id": target_tenant,
                "project_id": target_project,
                "corpus_id": target_corpus,
            },
            "qdrant_payload_updated": qdrant_updated,
            "qdrant_error": qdrant_error,
        }

    @router.delete("/maintenance/dangling-run/{run_id}")
    async def delete_dangling_run(run_id: int) -> dict[str, Any]:
        """Delete a DB-only WorkflowRun that has NO matching Qdrant chunks.

        This is the inverse of orphan cleanup: the run exists in the DB but
        zero Qdrant points reference its (tenant, project, doc_id, doc_version).
        """
        with session_factory() as session:
            run = session.get(WorkflowRun, run_id)
            if run is None:
                from fastapi import HTTPException
                raise HTTPException(status_code=404, detail=f"WorkflowRun {run_id} not found")

            # Verify it really is dangling (no Qdrant points).
            store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection=qdrant_collection())
            must = [
                qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=str(run.tenant_id))),
                qm.FieldCondition(key="project_id", match=qm.MatchValue(value=str(run.project_id))),
                qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=str(run.doc_id))),
                qm.FieldCondition(key="doc_version", match=qm.MatchValue(value=str(run.doc_version))),
            ]
            pts = await run_in_threadpool(store.scroll_points, must=must, limit=1, max_points=1)
            if pts:
                from fastapi import HTTPException
                raise HTTPException(
                    status_code=409,
                    detail=f"Run {run_id} still has Qdrant chunks; not dangling",
                )

            doc_id = str(run.doc_id)
            session.delete(run)
            session.commit()

        return {"ok": True, "deleted_run_id": run_id, "doc_id": doc_id}

    @router.delete("/docs/{doc_id}")
    async def delete_doc(
        doc_id: str,
        tenant_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
        corpus_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """Delete all Qdrant chunks and active-version row for a document."""
        from atlas.admin._helpers import qdrant_post_json

        t_id = tenant_id or settings.atlas_default_tenant_id
        p_id = project_id or settings.atlas_default_project_id
        c_id = corpus_id or settings.atlas_default_corpus_id

        # Remove all Qdrant points for this doc in this scope.
        collection = qdrant_collection()
        delete_filter: dict[str, Any] = {
            "must": [
                {"key": "tenant_id", "match": {"value": t_id}},
                {"key": "project_id", "match": {"value": p_id}},
                {"key": "doc_id", "match": {"value": doc_id}},
            ]
        }
        res = await qdrant_post_json(settings,
            f"/collections/{collection}/points/delete",
            {"filter": delete_filter, "wait": True},
        )
        points_deleted = (res.get("result") or {}).get("operation_id", 0)

        # Remove the active-doc-version row (best-effort).
        rows_deleted = 0
        try:
            with session_factory() as session:
                stmt = (
                    session.query(ActiveDocVersion)
                    .filter(
                        ActiveDocVersion.tenant_id == t_id,
                        ActiveDocVersion.project_id == p_id,
                        ActiveDocVersion.doc_id == doc_id,
                    )
                )
                rows = stmt.all()
                for row in rows:
                    session.delete(row)
                session.commit()
                rows_deleted = len(rows)
        except Exception:  # noqa: BLE001
            rows_deleted = 0

        return {
            "ok": True,
            "doc_id": doc_id,
            "tenant_id": t_id,
            "project_id": p_id,
            "corpus_id": c_id,
            "qdrant_operation_id": points_deleted,
            "active_version_rows_deleted": rows_deleted,
        }
