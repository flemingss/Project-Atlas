from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ConfigVersion(Base):
    __tablename__ = "config_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.timezone.utc),
    )

    name: Mapped[str] = mapped_column(String(120), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)

    # Full snapshot of effective config (merged YAML + overrides)
    config_hash: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON)

    notes: Mapped[str] = mapped_column(Text, default="")


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.timezone.utc),
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.timezone.utc),
        onupdate=lambda: dt.datetime.now(dt.timezone.utc),
    )

    tenant_id: Mapped[str] = mapped_column(String(120), index=True)
    project_id: Mapped[str] = mapped_column(String(120), index=True)
    doc_id: Mapped[str] = mapped_column(String(256), index=True)
    doc_version: Mapped[str] = mapped_column(String(64), default="1")

    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    current_node: Mapped[str] = mapped_column(String(64), default="ingest")

    error_code: Mapped[str] = mapped_column(String(64), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")

    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class NodeRun(Base):
    __tablename__ = "node_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.timezone.utc),
    )

    node_name: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)

    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.timezone.utc),
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    duration_ms: Mapped[float | None] = mapped_column(default=None)

    input_ref: Mapped[str] = mapped_column(Text, default="")
    output_ref: Mapped[str] = mapped_column(Text, default="")

    error_code: Mapped[str] = mapped_column(String(64), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")


class ArtifactRef(Base):
    __tablename__ = "artifact_refs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True)
    node_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("node_runs.id", ondelete="SET NULL"),
        index=True,
        default=None,
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.timezone.utc),
    )

    kind: Mapped[str] = mapped_column(String(64), index=True)
    path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    mime_type: Mapped[str] = mapped_column(String(120), default="")

    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class HitlTaskRow(Base):
    __tablename__ = "hitl_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.timezone.utc),
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.timezone.utc),
        onupdate=lambda: dt.datetime.now(dt.timezone.utc),
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    run_id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True)

    tenant_id: Mapped[str] = mapped_column(String(120), index=True)
    project_id: Mapped[str] = mapped_column(String(120), index=True)
    doc_id: Mapped[str] = mapped_column(String(256), index=True)
    doc_version: Mapped[str] = mapped_column(String(64), default="1")

    chunk_id: Mapped[str] = mapped_column(String(256), default="")

    priority_score: Mapped[float] = mapped_column(default=0.0, index=True)
    is_sensitive: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    judge_score: Mapped[float] = mapped_column(default=0.0)

    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    assigned_to: Mapped[str] = mapped_column(String(256), default="")

    before_md: Mapped[str] = mapped_column(Text, default="")
    after_md: Mapped[str] = mapped_column(Text, default="")
    reason_for_edit: Mapped[str] = mapped_column(Text, default="")

    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class ActiveDocVersion(Base):
    __tablename__ = "active_doc_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "project_id", "doc_id", name="uq_active_doc_versions_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.timezone.utc),
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.timezone.utc),
        onupdate=lambda: dt.datetime.now(dt.timezone.utc),
    )

    tenant_id: Mapped[str] = mapped_column(String(120), index=True)
    project_id: Mapped[str] = mapped_column(String(120), index=True)
    doc_id: Mapped[str] = mapped_column(String(256), index=True)

    active_doc_version: Mapped[str] = mapped_column(String(64), default="1")
