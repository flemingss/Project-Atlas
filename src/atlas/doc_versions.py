from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.models import ActiveDocVersion, WorkflowRun


def get_active_doc_version(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    doc_id: str,
) -> str | None:
    row = session.execute(
        select(ActiveDocVersion)
        .where(ActiveDocVersion.tenant_id == tenant_id)
        .where(ActiveDocVersion.project_id == project_id)
        .where(ActiveDocVersion.doc_id == doc_id)
        .limit(1)
    ).scalars().first()
    if row is None:
        return None
    return str(row.active_doc_version)


def set_active_doc_version(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    doc_id: str,
    doc_version: str,
) -> ActiveDocVersion:
    row = session.execute(
        select(ActiveDocVersion)
        .where(ActiveDocVersion.tenant_id == tenant_id)
        .where(ActiveDocVersion.project_id == project_id)
        .where(ActiveDocVersion.doc_id == doc_id)
        .limit(1)
    ).scalars().first()

    if row is None:
        row = ActiveDocVersion(
            tenant_id=tenant_id,
            project_id=project_id,
            doc_id=doc_id,
            active_doc_version=str(doc_version),
        )
        session.add(row)
    else:
        row.active_doc_version = str(doc_version)

    session.commit()
    session.refresh(row)
    return row


def get_latest_doc_version_from_runs(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    doc_id: str,
) -> str | None:
    row = session.execute(
        select(WorkflowRun.doc_version)
        .where(WorkflowRun.tenant_id == tenant_id)
        .where(WorkflowRun.project_id == project_id)
        .where(WorkflowRun.doc_id == doc_id)
        .order_by(WorkflowRun.id.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    return str(row[0])
