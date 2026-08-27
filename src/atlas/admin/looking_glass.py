"""Looking-glass monitoring / debugging routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from atlas.feedback_ledger import feedback_category_counts
from atlas.hitl_ledger import HitlTaskResponse, list_hitl_tasks, to_hitl_response
from atlas.models import HitlTaskRow, NodeRun, WorkflowRun
from atlas.settings import Settings
from atlas.workflow_ledger import WorkflowRunResponse, to_run_response

from ._helpers import (
    ledger_summary,
    parse_cursor,
    qdrant_collection,
    qdrant_get_json,
    qdrant_post_json,
)


class _CollectionNotFound(Exception):
    """Raised when the Qdrant collection does not exist yet."""


async def _safe_qdrant_get(settings: Settings, path: str) -> dict[str, Any]:
    """Like ``qdrant_get_json`` but raises ``_CollectionNotFound`` on 404."""
    import httpx

    try:
        return await qdrant_get_json(settings, path)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise _CollectionNotFound() from exc
        raise


async def _safe_qdrant_post(settings: Settings, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Like ``qdrant_post_json`` but raises ``_CollectionNotFound`` on 404."""
    import httpx

    try:
        return await qdrant_post_json(settings, path, payload)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise _CollectionNotFound() from exc
        raise


def register_looking_glass_routes(
    r: APIRouter,
    *,
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    """Register ``/looking-glass/*`` monitoring endpoints on *r*."""

    @r.get("/looking-glass/qdrant")
    async def looking_glass_qdrant() -> dict[str, Any]:
        collection = qdrant_collection()
        try:
            info = await _safe_qdrant_get(settings, f"/collections/{collection}")
            count = await _safe_qdrant_post(
                settings,
                f"/collections/{collection}/points/count",
                {"exact": True, "filter": {}},
            )
        except _CollectionNotFound:
            return {
                "collection": collection,
                "collection_info": None,
                "points_count": None,
                "status": "collection_not_found",
            }
        return {
            "collection": collection,
            "collection_info": info.get("result"),
            "points_count": (count.get("result") or {}).get("count"),
        }

    @r.get("/looking-glass/ledger/summary")
    def looking_glass_ledger_summary() -> dict[str, Any]:
        with session_factory() as session:
            return ledger_summary(session)

    @r.get("/looking-glass/ledger/in-flight", response_model=list[WorkflowRunResponse])
    def looking_glass_in_flight(
        limit: int = Query(default=50, ge=1, le=500),
        tenant_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
    ) -> list[WorkflowRunResponse]:
        with session_factory() as session:
            stmt = (
                select(WorkflowRun)
                .where(WorkflowRun.status.not_in(["completed", "failed"]))
                .order_by(WorkflowRun.updated_at.desc(), WorkflowRun.id.desc())
                .limit(int(limit))
            )
            if tenant_id:
                stmt = stmt.where(WorkflowRun.tenant_id == tenant_id)
            if project_id:
                stmt = stmt.where(WorkflowRun.project_id == project_id)
            rows = list(session.execute(stmt).scalars().all())
            return [to_run_response(r) for r in rows]

    @r.get("/looking-glass/ledger/failures")
    def looking_glass_failures(
        limit: int = Query(default=50, ge=1, le=500),
        tenant_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        with session_factory() as session:
            run_stmt = (
                select(WorkflowRun)
                .where(WorkflowRun.status == "failed")
                .order_by(WorkflowRun.updated_at.desc(), WorkflowRun.id.desc())
                .limit(int(limit))
            )
            if tenant_id:
                run_stmt = run_stmt.where(WorkflowRun.tenant_id == tenant_id)
            if project_id:
                run_stmt = run_stmt.where(WorkflowRun.project_id == project_id)
            runs = list(session.execute(run_stmt).scalars().all())

            failures: list[dict[str, Any]] = []
            for run in runs:
                from atlas.workflow_ledger import to_node_run_response

                node_stmt = (
                    select(NodeRun)
                    .where(NodeRun.run_id == run.id)
                    .where((NodeRun.status == "failed") | (NodeRun.error_code != "") | (NodeRun.error_message != ""))
                    .order_by(NodeRun.id.desc())
                    .limit(25)
                )
                nodes = list(session.execute(node_stmt).scalars().all())
                failures.append(
                    {
                        "run": to_run_response(run).model_dump(),
                        "node_errors": [to_node_run_response(n).model_dump() for n in nodes],
                    }
                )

            return {"failures": failures}

    @r.get("/looking-glass/ledger/hitl", response_model=list[HitlTaskResponse])
    def looking_glass_hitl(
        status: str | None = Query(default="pending"),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> list[HitlTaskResponse]:
        with session_factory() as session:
            rows = list_hitl_tasks(session, status=status or None, limit=int(limit))
            return [to_hitl_response(r) for r in rows]

    @r.get("/looking-glass/inventory")
    async def looking_glass_inventory(
        max_points: int = Query(default=5000, ge=1, le=200000),
        page_size: int = Query(default=500, ge=50, le=2000),
    ) -> dict[str, Any]:
        with session_factory() as session:
            ledger = ledger_summary(session)

        collection = qdrant_collection()
        offset: str | int | None = None
        scanned = 0
        truncated = False

        unique_docs: set[str] = set()
        unique_tenants: set[str] = set()
        unique_projects: set[str] = set()

        points_by_tenant: dict[str, int] = {}
        points_by_project: dict[str, int] = {}
        points_by_tenant_project: dict[str, int] = {}
        points_finalized: int = 0
        points_nonfinalized: int = 0

        while scanned < max_points:
            body: dict[str, Any] = {
                "limit": int(min(page_size, max_points - scanned)),
                "with_payload": True,
                "with_vector": False,
            }
            if offset is not None:
                body["offset"] = offset

            try:
                res = await _safe_qdrant_post(settings, f"/collections/{collection}/points/scroll", body)
            except _CollectionNotFound:
                break
            result = res.get("result") or {}
            points = result.get("points") or []
            offset = result.get("next_page_offset")

            if not points:
                break

            for p in points:
                scanned += 1
                payload = p.get("payload") or {}
                doc_id = payload.get("doc_id")
                tenant_id = payload.get("tenant_id")
                project_id = payload.get("project_id")
                is_finalized = payload.get("is_finalized")

                if doc_id:
                    unique_docs.add(str(doc_id))
                if tenant_id:
                    unique_tenants.add(str(tenant_id))
                    points_by_tenant[str(tenant_id)] = points_by_tenant.get(str(tenant_id), 0) + 1
                if project_id:
                    unique_projects.add(str(project_id))
                    points_by_project[str(project_id)] = points_by_project.get(str(project_id), 0) + 1

                if tenant_id and project_id:
                    key = f"{tenant_id}/{project_id}"
                    points_by_tenant_project[key] = points_by_tenant_project.get(key, 0) + 1

                if is_finalized is True:
                    points_finalized += 1
                elif is_finalized is False:
                    points_nonfinalized += 1

                if scanned >= max_points:
                    break

            if scanned >= max_points and offset is not None:
                truncated = True
                break
            if offset is None:
                break

        return {
            "collection": collection,
            "ledger": ledger,
            "scanned_points": scanned,
            "truncated": truncated,
            "unique_docs": len(unique_docs),
            "unique_tenants": len(unique_tenants),
            "unique_projects": len(unique_projects),
            "points_finalized": points_finalized,
            "points_nonfinalized": points_nonfinalized,
            "points_by_tenant": points_by_tenant,
            "points_by_project": points_by_project,
            "points_by_tenant_project": points_by_tenant_project,
        }

    @r.get("/looking-glass/docs")
    async def looking_glass_docs(
        limit: int = Query(default=50, ge=1, le=200),
        cursor: str | None = Query(default=None),
        scan_page_size: int = Query(default=200, ge=50, le=1000),
        tenant_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
        corpus_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        collection = qdrant_collection()
        next_offset = parse_cursor(cursor)

        docs: dict[str, dict[str, Any]] = {}
        scanned_pages = 0

        scope_must: list[dict[str, Any]] = []
        if tenant_id:
            scope_must.append({"key": "tenant_id", "match": {"value": tenant_id}})
        if project_id:
            scope_must.append({"key": "project_id", "match": {"value": project_id}})
        if corpus_id:
            scope_must.append({"key": "corpus_id", "match": {"value": corpus_id}})

        while len(docs) < limit and scanned_pages < 10:
            scanned_pages += 1
            body: dict[str, Any] = {
                "limit": int(scan_page_size),
                "with_payload": True,
                "with_vector": False,
            }
            if next_offset is not None:
                body["offset"] = next_offset
            if scope_must:
                body["filter"] = {"must": scope_must}

            try:
                res = await _safe_qdrant_post(settings, f"/collections/{collection}/points/scroll", body)
            except _CollectionNotFound:
                break
            result = res.get("result") or {}
            points = result.get("points") or []
            next_offset = result.get("next_page_offset")

            for p in points:
                payload = p.get("payload") or {}
                doc_id = payload.get("doc_id")
                if not doc_id or doc_id in docs:
                    continue
                docs[str(doc_id)] = {
                    "doc_id": str(doc_id),
                    "tenant_id": payload.get("tenant_id"),
                    "project_id": payload.get("project_id"),
                    "corpus_id": payload.get("corpus_id"),
                    "doc_version": payload.get("doc_version"),
                    "source_mime_type": payload.get("source_mime_type"),
                    "is_finalized": payload.get("is_finalized"),
                    "is_sensitive": payload.get("is_sensitive"),
                    "created_at": payload.get("created_at"),
                }
                if len(docs) >= limit:
                    break

            if not points or next_offset is None:
                break

        return {
            "collection": collection,
            "docs": list(docs.values()),
            "next_cursor": None if next_offset is None else str(next_offset),
        }

    @r.get("/looking-glass/docs/{doc_id}")
    async def looking_glass_doc_detail(
        doc_id: str,
        limit: int = Query(default=100, ge=1, le=500),
        cursor: str | None = Query(default=None),
    ) -> dict[str, Any]:
        collection = qdrant_collection()
        next_offset = parse_cursor(cursor)

        body: dict[str, Any] = {
            "limit": int(limit),
            "with_payload": True,
            "with_vector": False,
            "filter": {"must": [{"key": "doc_id", "match": {"value": doc_id}}]},
        }
        if next_offset is not None:
            body["offset"] = next_offset

        try:
            res = await _safe_qdrant_post(settings, f"/collections/{collection}/points/scroll", body)
        except _CollectionNotFound:
            return {
                "collection": collection,
                "doc_id": doc_id,
                "chunks": [],
                "next_cursor": None,
            }
        result = res.get("result") or {}
        points = result.get("points") or []
        next_offset = result.get("next_page_offset")

        chunks: list[dict[str, Any]] = []
        for p in points:
            payload = p.get("payload") or {}
            chunks.append(
                {
                    "id": str(p.get("id")),
                    "chunk_index": payload.get("chunk_index"),
                    "text": payload.get("text"),
                    "content_hash": payload.get("content_hash"),
                    "created_at": payload.get("created_at"),
                    "tenant_id": payload.get("tenant_id"),
                    "project_id": payload.get("project_id"),
                    "doc_version": payload.get("doc_version"),
                    "is_finalized": payload.get("is_finalized"),
                    "is_sensitive": payload.get("is_sensitive"),
                }
            )

        return {
            "collection": collection,
            "doc_id": doc_id,
            "chunks": chunks,
            "next_cursor": None if next_offset is None else str(next_offset),
        }

    @r.get("/looking-glass/docs/{doc_id}/chunks/{chunk_index}")
    async def looking_glass_chunk_preview(doc_id: str, chunk_index: int) -> dict[str, Any]:
        collection = qdrant_collection()
        body: dict[str, Any] = {
            "limit": 1,
            "with_payload": True,
            "with_vector": False,
            "filter": {
                "must": [
                    {"key": "doc_id", "match": {"value": doc_id}},
                    {"key": "chunk_index", "match": {"value": int(chunk_index)}},
                ]
            },
        }
        res = await _safe_qdrant_post(settings, f"/collections/{collection}/points/scroll", body)
        result = res.get("result") or {}
        points = result.get("points") or []
        if not points:
            return {"ok": False, "detail": "not found"}
        p = points[0]
        return {
            "ok": True,
            "collection": collection,
            "doc_id": doc_id,
            "chunk_index": int(chunk_index),
            "id": str(p.get("id")),
            "payload": p.get("payload") or {},
        }

    # ── Metrics aggregation (Phase 7C) ───────────────────────────────

    @r.get("/looking-glass/metrics")
    def looking_glass_metrics(
        tenant_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
        corpus_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """Aggregated pipeline quality metrics — optionally scoped."""
        with session_factory() as session:
            return _build_metrics(session, tenant_id=tenant_id, project_id=project_id, corpus_id=corpus_id)

    def _build_metrics(
        session: Session,
        *,
        tenant_id: str | None,
        project_id: str | None,
        corpus_id: str | None,
    ) -> dict[str, Any]:
        """Assemble the metrics payload."""

        run_stmt = select(WorkflowRun.status, func.count()).select_from(WorkflowRun)
        if tenant_id:
            run_stmt = run_stmt.where(WorkflowRun.tenant_id == tenant_id)
        if project_id:
            run_stmt = run_stmt.where(WorkflowRun.project_id == project_id)
        run_stmt = run_stmt.group_by(WorkflowRun.status)
        run_dist = {str(k): int(v) for k, v in session.execute(run_stmt).all() if k is not None}
        run_total = sum(run_dist.values()) or 0

        node_base = select(NodeRun).join(WorkflowRun, NodeRun.run_id == WorkflowRun.id)
        if tenant_id:
            node_base = node_base.where(WorkflowRun.tenant_id == tenant_id)
        if project_id:
            node_base = node_base.where(WorkflowRun.project_id == project_id)

        node_total_q = select(func.count()).select_from(node_base.subquery())
        node_total = session.execute(node_total_q).scalar_one()

        node_fail_base = node_base.where((NodeRun.status == "failed") | (NodeRun.error_code != ""))
        node_fail_count = session.execute(
            select(func.count()).select_from(node_fail_base.subquery())
        ).scalar_one()

        node_by_name = select(
            NodeRun.node_name, func.count()
        ).join(WorkflowRun, NodeRun.run_id == WorkflowRun.id).where(
            (NodeRun.status == "failed") | (NodeRun.error_code != "")
        )
        if tenant_id:
            node_by_name = node_by_name.where(WorkflowRun.tenant_id == tenant_id)
        if project_id:
            node_by_name = node_by_name.where(WorkflowRun.project_id == project_id)
        node_by_name = node_by_name.group_by(NodeRun.node_name)
        node_fail_by_name = {str(k): int(v) for k, v in session.execute(node_by_name).all() if k is not None}

        hitl_stmt = select(HitlTaskRow.status, func.count()).select_from(HitlTaskRow)
        if tenant_id:
            hitl_stmt = hitl_stmt.where(HitlTaskRow.tenant_id == tenant_id)
        if project_id:
            hitl_stmt = hitl_stmt.where(HitlTaskRow.project_id == project_id)
        hitl_stmt = hitl_stmt.group_by(HitlTaskRow.status)
        hitl_dist = {str(k): int(v) for k, v in session.execute(hitl_stmt).all() if k is not None}
        hitl_total = sum(hitl_dist.values()) or 0

        fb_counts = feedback_category_counts(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            corpus_id=corpus_id,
        )

        completed = run_dist.get("completed", 0)
        failed = run_dist.get("failed", 0)
        auto_accepted = max(0, completed - hitl_total) if completed else 0

        return {
            "scope": {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "corpus_id": corpus_id,
            },
            "workflow_runs": {
                "total": run_total,
                "by_status": run_dist,
                "completion_rate": round(completed / run_total, 4) if run_total else 0,
                "failure_rate": round(failed / run_total, 4) if run_total else 0,
            },
            "node_runs": {
                "total": int(node_total),
                "failed": int(node_fail_count),
                "failure_rate": round(int(node_fail_count) / int(node_total), 4) if node_total else 0,
                "failures_by_node": node_fail_by_name,
            },
            "hitl": {
                "total": hitl_total,
                "by_status": hitl_dist,
                "escalation_rate": round(hitl_total / run_total, 4) if run_total else 0,
            },
            "auto_accepted": {
                "count": auto_accepted,
                "rate": round(auto_accepted / run_total, 4) if run_total else 0,
            },
            "cleanup_feedback": {
                "total": sum(fb_counts.values()),
                "by_category": fb_counts,
            },
        }
