from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.models import ArtifactRef, NodeRun, WorkflowRun


class WorkflowRunCreateRequest(BaseModel):
    tenant_id: str
    project_id: str
    doc_id: str
    doc_version: str = "1"

    status: str = "pending"
    current_node: str = "ingest"

    meta: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunResponse(BaseModel):
    id: int
    created_at: dt.datetime
    updated_at: dt.datetime

    tenant_id: str
    project_id: str
    doc_id: str
    doc_version: str

    status: str
    current_node: str

    error_code: str
    error_message: str

    meta: dict[str, Any]


class NodeRunCreateRequest(BaseModel):
    node_name: str
    status: str = "running"

    input_ref: str = ""
    output_ref: str = ""

    error_code: str = ""
    error_message: str = ""


class NodeRunResponse(BaseModel):
    id: int
    run_id: int
    created_at: dt.datetime

    node_name: str
    status: str

    started_at: dt.datetime
    completed_at: dt.datetime | None
    duration_ms: float | None

    input_ref: str
    output_ref: str

    error_code: str
    error_message: str


class ArtifactRefCreateRequest(BaseModel):
    kind: str
    path: str

    node_run_id: int | None = None
    sha256: str = ""
    mime_type: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


class ArtifactRefResponse(BaseModel):
    id: int
    run_id: int
    node_run_id: int | None
    created_at: dt.datetime

    kind: str
    path: str
    sha256: str
    mime_type: str

    meta: dict[str, Any]


def create_workflow_run(session: Session, *, req: WorkflowRunCreateRequest) -> WorkflowRun:
    row = WorkflowRun(
        tenant_id=req.tenant_id,
        project_id=req.project_id,
        doc_id=req.doc_id,
        doc_version=req.doc_version,
        status=req.status,
        current_node=req.current_node,
        meta=req.meta,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def get_workflow_run(session: Session, *, run_id: int) -> WorkflowRun | None:
    res = session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
    return res.scalars().first()


def get_latest_run_by_doc_id(session: Session, *, doc_id: str) -> WorkflowRun | None:
    """Return the most recent WorkflowRun for a given *doc_id*.

    Orders by ``id DESC`` so the latest ingest run is returned first.
    Returns ``None`` when no runs exist for the document.
    """
    res = session.execute(
        select(WorkflowRun)
        .where(WorkflowRun.doc_id == doc_id)
        .order_by(WorkflowRun.id.desc())
        .limit(1)
    )
    return res.scalars().first()


def list_workflow_runs(
    session: Session,
    *,
    limit: int = 100,
    tenant_id: str | None = None,
    project_id: str | None = None,
) -> list[WorkflowRun]:
    stmt = select(WorkflowRun)
    if tenant_id:
        stmt = stmt.where(WorkflowRun.tenant_id == tenant_id)
    if project_id:
        stmt = stmt.where(WorkflowRun.project_id == project_id)
    stmt = stmt.order_by(WorkflowRun.id.desc()).limit(int(limit))
    res = session.execute(stmt)
    return list(res.scalars().all())


def create_node_run(session: Session, *, run_id: int, req: NodeRunCreateRequest) -> NodeRun:
    row = NodeRun(
        run_id=run_id,
        node_name=req.node_name,
        status=req.status,
        input_ref=req.input_ref,
        output_ref=req.output_ref,
        error_code=req.error_code,
        error_message=req.error_message,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def list_node_runs(session: Session, *, run_id: int, limit: int = 500) -> list[NodeRun]:
    res = session.execute(
        select(NodeRun).where(NodeRun.run_id == run_id).order_by(NodeRun.id.asc()).limit(int(limit))
    )
    return list(res.scalars().all())


def add_artifact_ref(session: Session, *, run_id: int, req: ArtifactRefCreateRequest) -> ArtifactRef:
    row = ArtifactRef(
        run_id=run_id,
        node_run_id=req.node_run_id,
        kind=req.kind,
        path=req.path,
        sha256=req.sha256,
        mime_type=req.mime_type,
        meta=req.meta,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def list_artifact_refs(session: Session, *, run_id: int, limit: int = 500) -> list[ArtifactRef]:
    res = session.execute(
        select(ArtifactRef).where(ArtifactRef.run_id == run_id).order_by(ArtifactRef.id.asc()).limit(int(limit))
    )
    return list(res.scalars().all())


def to_run_response(row: WorkflowRun) -> WorkflowRunResponse:
    return WorkflowRunResponse(
        id=row.id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        tenant_id=row.tenant_id,
        project_id=row.project_id,
        doc_id=row.doc_id,
        doc_version=row.doc_version,
        status=row.status,
        current_node=row.current_node,
        error_code=row.error_code,
        error_message=row.error_message,
        meta=row.meta or {},
    )


def to_node_run_response(row: NodeRun) -> NodeRunResponse:
    return NodeRunResponse(
        id=row.id,
        run_id=row.run_id,
        created_at=row.created_at,
        node_name=row.node_name,
        status=row.status,
        started_at=row.started_at,
        completed_at=row.completed_at,
        duration_ms=row.duration_ms,
        input_ref=row.input_ref,
        output_ref=row.output_ref,
        error_code=row.error_code,
        error_message=row.error_message,
    )


def to_artifact_ref_response(row: ArtifactRef) -> ArtifactRefResponse:
    return ArtifactRefResponse(
        id=row.id,
        run_id=row.run_id,
        node_run_id=row.node_run_id,
        created_at=row.created_at,
        kind=row.kind,
        path=row.path,
        sha256=row.sha256,
        mime_type=row.mime_type,
        meta=row.meta or {},
    )
