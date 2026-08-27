"""Cleanup-feedback ledger — CRUD helpers for the ``cleanup_feedback`` table.

Operators (or automated hooks) create feedback entries to record quality
issues observed after the cleanup pipeline.  These entries are later used
by the metrics aggregation endpoint and can inform rule-tuning workflows.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from atlas.models import CleanupFeedback

# ---------------------------------------------------------------------------
# Request / Response schemas (Pydantic)
# ---------------------------------------------------------------------------

class FeedbackCreateRequest(BaseModel):
    tenant_id: str = ""
    project_id: str = ""
    corpus_id: str = ""
    doc_id: str = ""
    chunk_id: str = ""
    run_id: int | None = None

    category: str = "other"  # e.g. missed_header_strip, bad_bullet_fix, ocr_artefact, other
    description: str = ""

    source_span_start: int | None = None
    source_span_end: int | None = None

    created_by: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


class FeedbackResponse(BaseModel):
    id: int
    created_at: dt.datetime

    tenant_id: str
    project_id: str
    corpus_id: str
    doc_id: str
    chunk_id: str
    run_id: int | None

    category: str
    description: str
    source_span_start: int | None
    source_span_end: int | None
    created_by: str
    meta: dict[str, Any]


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------

def create_feedback(session: Session, *, req: FeedbackCreateRequest) -> CleanupFeedback:
    row = CleanupFeedback(
        tenant_id=req.tenant_id,
        project_id=req.project_id,
        corpus_id=req.corpus_id,
        doc_id=req.doc_id,
        chunk_id=req.chunk_id,
        run_id=req.run_id,
        category=req.category,
        description=req.description,
        source_span_start=req.source_span_start,
        source_span_end=req.source_span_end,
        created_by=req.created_by,
        meta=req.meta,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def get_feedback(session: Session, *, feedback_id: int) -> CleanupFeedback | None:
    res = session.execute(select(CleanupFeedback).where(CleanupFeedback.id == feedback_id))
    return res.scalars().first()


def list_feedback(
    session: Session,
    *,
    tenant_id: str | None = None,
    project_id: str | None = None,
    corpus_id: str | None = None,
    doc_id: str | None = None,
    category: str | None = None,
    limit: int = 100,
) -> list[CleanupFeedback]:
    stmt = select(CleanupFeedback)
    if tenant_id:
        stmt = stmt.where(CleanupFeedback.tenant_id == tenant_id)
    if project_id:
        stmt = stmt.where(CleanupFeedback.project_id == project_id)
    if corpus_id:
        stmt = stmt.where(CleanupFeedback.corpus_id == corpus_id)
    if doc_id:
        stmt = stmt.where(CleanupFeedback.doc_id == doc_id)
    if category:
        stmt = stmt.where(CleanupFeedback.category == category)
    stmt = stmt.order_by(CleanupFeedback.created_at.desc(), CleanupFeedback.id.desc()).limit(int(limit))
    res = session.execute(stmt)
    return list(res.scalars().all())


def delete_feedback(session: Session, *, feedback_id: int) -> bool:
    """Delete a feedback entry. Returns True if a row was deleted."""
    row = get_feedback(session, feedback_id=feedback_id)
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def feedback_category_counts(
    session: Session,
    *,
    tenant_id: str | None = None,
    project_id: str | None = None,
    corpus_id: str | None = None,
) -> dict[str, int]:
    """Return ``{category: count}`` aggregation, optionally scoped."""
    stmt = select(CleanupFeedback.category, func.count()).group_by(CleanupFeedback.category)
    if tenant_id:
        stmt = stmt.where(CleanupFeedback.tenant_id == tenant_id)
    if project_id:
        stmt = stmt.where(CleanupFeedback.project_id == project_id)
    if corpus_id:
        stmt = stmt.where(CleanupFeedback.corpus_id == corpus_id)
    rows = session.execute(stmt).all()
    return {str(cat): int(cnt) for cat, cnt in rows}


def to_feedback_response(row: CleanupFeedback) -> FeedbackResponse:
    return FeedbackResponse(
        id=row.id,
        created_at=row.created_at,
        tenant_id=row.tenant_id,
        project_id=row.project_id,
        corpus_id=row.corpus_id,
        doc_id=row.doc_id,
        chunk_id=row.chunk_id,
        run_id=row.run_id,
        category=row.category,
        description=row.description,
        source_span_start=row.source_span_start,
        source_span_end=row.source_span_end,
        created_by=row.created_by,
        meta=row.meta or {},
    )
