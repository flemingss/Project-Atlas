from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.models import HitlTaskRow

HitlStatus = Literal["pending", "in_progress", "completed", "skipped", "rejected"]


def compute_priority_score(*, is_sensitive: bool, judge_score: float) -> float:
    # HLD: High-sensitivity + Low Score = Top
    base_priority = 10.0 - float(judge_score)
    sensitivity_multiplier = 2.0 if bool(is_sensitive) else 1.0
    return float(base_priority * sensitivity_multiplier)


class HitlTaskCreateRequest(BaseModel):
    run_id: int

    tenant_id: str
    project_id: str
    doc_id: str
    doc_version: str = "1"

    chunk_id: str = ""

    is_sensitive: bool = False
    judge_score: float = 0.0

    before_md: str = ""

    assigned_to: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


class HitlTaskCompleteRequest(BaseModel):
    after_md: str
    reason_for_edit: str = ""


class HitlTaskSkipRequest(BaseModel):
    reason: str = ""


class HitlTaskRejectRequest(BaseModel):
    reason: str = ""


class HitlTaskResponse(BaseModel):
    id: int
    created_at: dt.datetime
    updated_at: dt.datetime
    completed_at: dt.datetime | None

    run_id: int

    tenant_id: str
    project_id: str
    doc_id: str
    doc_version: str

    chunk_id: str

    priority_score: float
    is_sensitive: bool
    judge_score: float

    status: str
    assigned_to: str

    before_md: str
    after_md: str
    reason_for_edit: str

    meta: dict[str, Any]


def create_hitl_task(session: Session, *, req: HitlTaskCreateRequest) -> HitlTaskRow:
    row = HitlTaskRow(
        run_id=req.run_id,
        tenant_id=req.tenant_id,
        project_id=req.project_id,
        doc_id=req.doc_id,
        doc_version=req.doc_version,
        chunk_id=req.chunk_id,
        priority_score=compute_priority_score(is_sensitive=req.is_sensitive, judge_score=req.judge_score),
        is_sensitive=req.is_sensitive,
        judge_score=req.judge_score,
        status="pending",
        assigned_to=req.assigned_to,
        before_md=req.before_md,
        meta=req.meta,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def get_hitl_task(session: Session, *, task_id: int) -> HitlTaskRow | None:
    res = session.execute(select(HitlTaskRow).where(HitlTaskRow.id == task_id))
    return res.scalars().first()


def list_hitl_tasks(
    session: Session,
    *,
    status: str | None = None,
    limit: int = 100,
    tenant_id: str | None = None,
    project_id: str | None = None,
) -> list[HitlTaskRow]:
    stmt = select(HitlTaskRow)
    if status:
        stmt = stmt.where(HitlTaskRow.status == status)
    if tenant_id:
        stmt = stmt.where(HitlTaskRow.tenant_id == tenant_id)
    if project_id:
        stmt = stmt.where(HitlTaskRow.project_id == project_id)
    stmt = stmt.order_by(HitlTaskRow.priority_score.desc(), HitlTaskRow.id.asc()).limit(int(limit))
    res = session.execute(stmt)
    return list(res.scalars().all())


def claim_next_task(session: Session, *, assigned_to: str = "") -> HitlTaskRow | None:
    # Simple claim implementation (portable across SQLite/Postgres). This may allow races
    # under high concurrency; sufficient for current RC scope.
    stmt = (
        select(HitlTaskRow)
        .where(HitlTaskRow.status == "pending")
        .order_by(HitlTaskRow.priority_score.desc(), HitlTaskRow.id.asc())
        .limit(1)
    )
    row = session.execute(stmt).scalars().first()
    if row is None:
        return None

    row.status = "in_progress"
    if assigned_to:
        row.assigned_to = assigned_to
    session.commit()
    session.refresh(row)
    return row


def complete_task(session: Session, *, task_id: int, req: HitlTaskCompleteRequest) -> HitlTaskRow:
    row = get_hitl_task(session, task_id=task_id)
    if row is None:
        raise KeyError("task not found")
    if row.status != "in_progress":
        raise ValueError("task must be in_progress to complete")

    row.status = "completed"
    row.after_md = req.after_md
    row.reason_for_edit = req.reason_for_edit
    row.completed_at = dt.datetime.now(dt.UTC)
    session.commit()
    session.refresh(row)
    return row


def skip_task(session: Session, *, task_id: int, req: HitlTaskSkipRequest) -> HitlTaskRow:
    row = get_hitl_task(session, task_id=task_id)
    if row is None:
        raise KeyError("task not found")
    if row.status not in ("pending", "in_progress"):
        raise ValueError("task must be pending or in_progress to skip")

    row.status = "skipped"
    row.reason_for_edit = req.reason
    row.completed_at = dt.datetime.now(dt.UTC)
    session.commit()
    session.refresh(row)
    return row


def reject_task(session: Session, *, task_id: int, req: HitlTaskRejectRequest) -> HitlTaskRow:
    row = get_hitl_task(session, task_id=task_id)
    if row is None:
        raise KeyError("task not found")
    if row.status not in ("pending", "in_progress"):
        raise ValueError("task must be pending or in_progress to reject")

    row.status = "rejected"
    row.reason_for_edit = req.reason
    row.completed_at = dt.datetime.now(dt.UTC)
    session.commit()
    session.refresh(row)
    return row


def to_hitl_response(row: HitlTaskRow) -> HitlTaskResponse:
    return HitlTaskResponse(
        id=row.id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
        run_id=row.run_id,
        tenant_id=row.tenant_id,
        project_id=row.project_id,
        doc_id=row.doc_id,
        doc_version=row.doc_version,
        chunk_id=row.chunk_id,
        priority_score=float(row.priority_score),
        is_sensitive=bool(row.is_sensitive),
        judge_score=float(row.judge_score),
        status=row.status,
        assigned_to=row.assigned_to,
        before_md=row.before_md,
        after_md=row.after_md,
        reason_for_edit=row.reason_for_edit,
        meta=row.meta or {},
    )
