"""Shared helper functions used across admin sub-modules."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from atlas.models import HitlTaskRow, NodeRun, WorkflowRun

# ── Qdrant helpers ───────────────────────────────────────────────────

def qdrant_url(settings: Any) -> str:
    return settings.atlas_qdrant_url.rstrip("/")


def qdrant_collection() -> str:
    return "atlas_chunks"


async def qdrant_get_json(settings: Any, path: str) -> dict[str, Any]:
    import httpx

    url = f"{qdrant_url(settings)}{path}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


async def qdrant_post_json(settings: Any, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    import httpx

    url = f"{qdrant_url(settings)}{path}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()


# ── Scope / cursor helpers ───────────────────────────────────────────

def clean_scope_id(label: str, value: str) -> str:
    v = (value or "").strip()
    if not v:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=f"{label} must be non-empty")
    return v


def parse_cursor(cursor: str | None) -> str | int | None:
    if cursor is None or cursor == "":
        return None
    if cursor.isdigit():
        return int(cursor)
    return cursor


# ── Ledger / stats helpers ───────────────────────────────────────────

def group_count(
    session: Session,
    *,
    label_col: Any,
    from_table: Any,
    where: Any | None = None,
) -> dict[str, int]:
    stmt = select(label_col, func.count()).select_from(from_table)
    if where is not None:
        stmt = stmt.where(where)
    stmt = stmt.group_by(label_col)
    res = session.execute(stmt).all()
    out: dict[str, int] = {}
    for k, v in res:
        if k is None:
            continue
        out[str(k)] = int(v)
    return out


def ledger_summary(session: Session) -> dict[str, Any]:
    import datetime as _dt

    cutoff_24h: _dt.datetime = _dt.datetime.now(_dt.UTC) - _dt.timedelta(hours=24)

    run_status = group_count(session, label_col=WorkflowRun.status, from_table=WorkflowRun)
    run_node = group_count(session, label_col=WorkflowRun.current_node, from_table=WorkflowRun)
    run_failed_codes = group_count(
        session,
        label_col=WorkflowRun.error_code,
        from_table=WorkflowRun,
        where=(WorkflowRun.error_code != ""),
    )

    node_status = group_count(session, label_col=NodeRun.status, from_table=NodeRun)
    node_name = group_count(session, label_col=NodeRun.node_name, from_table=NodeRun)
    node_failed_codes = group_count(
        session,
        label_col=NodeRun.error_code,
        from_table=NodeRun,
        where=(NodeRun.error_code != ""),
    )

    hitl_status = group_count(session, label_col=HitlTaskRow.status, from_table=HitlTaskRow)
    hitl_unassigned_pending = session.execute(
        select(func.count())
        .select_from(HitlTaskRow)
        .where(HitlTaskRow.status == "pending")
        .where(HitlTaskRow.assigned_to == "")
    ).scalar_one()

    run_total = session.execute(select(func.count()).select_from(WorkflowRun)).scalar_one()
    node_total = session.execute(select(func.count()).select_from(NodeRun)).scalar_one()
    hitl_total = session.execute(select(func.count()).select_from(HitlTaskRow)).scalar_one()

    docs_unique = session.execute(
        select(func.count(func.distinct(WorkflowRun.doc_id))).select_from(WorkflowRun)
    ).scalar_one()

    runs_last_24h = session.execute(
        select(func.count())
        .select_from(WorkflowRun)
        .where(WorkflowRun.created_at >= cutoff_24h)
    ).scalar_one()

    return {
        "workflow_runs": {
            "total": int(run_total),
            "docs_unique": int(docs_unique),
            "runs_last_24h": int(runs_last_24h),
            "by_status": run_status,
            "by_current_node": run_node,
            "failed_by_error_code": run_failed_codes,
        },
        "node_runs": {
            "total": int(node_total),
            "by_status": node_status,
            "by_node_name": node_name,
            "failed_by_error_code": node_failed_codes,
        },
        "hitl_tasks": {
            "total": int(hitl_total),
            "by_status": hitl_status,
            "pending_unassigned": int(hitl_unassigned_pending),
        },
    }
