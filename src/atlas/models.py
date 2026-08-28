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
        default=lambda: dt.datetime.now(dt.UTC),
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
        default=lambda: dt.datetime.now(dt.UTC),
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
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
        default=lambda: dt.datetime.now(dt.UTC),
    )

    node_name: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)

    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
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
        default=lambda: dt.datetime.now(dt.UTC),
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
        default=lambda: dt.datetime.now(dt.UTC),
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
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
        default=lambda: dt.datetime.now(dt.UTC),
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
    )

    tenant_id: Mapped[str] = mapped_column(String(120), index=True)
    project_id: Mapped[str] = mapped_column(String(120), index=True)
    doc_id: Mapped[str] = mapped_column(String(256), index=True)

    active_doc_version: Mapped[str] = mapped_column(String(64), default="1")


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
    )

    tenant_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("tenant_id", "project_id", name="uq_projects_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
    )

    tenant_id: Mapped[str] = mapped_column(String(120), index=True)
    project_id: Mapped[str] = mapped_column(String(120), index=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class Corpus(Base):
    __tablename__ = "corpora"
    __table_args__ = (
        UniqueConstraint("tenant_id", "project_id", "corpus_id", name="uq_corpora_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
    )

    tenant_id: Mapped[str] = mapped_column(String(120), index=True)
    project_id: Mapped[str] = mapped_column(String(120), index=True)
    corpus_id: Mapped[str] = mapped_column(String(120), index=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class CleanupFeedback(Base):
    """Operator feedback on cleanup quality — used to inform future rule tuning.

    Each row captures a single observation about a document or chunk that
    passed through the cleanup pipeline with an undesirable outcome.
    """

    __tablename__ = "cleanup_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
    )

    # Scoping — at least doc_id should be set
    tenant_id: Mapped[str] = mapped_column(String(120), index=True, default="")
    project_id: Mapped[str] = mapped_column(String(120), index=True, default="")
    corpus_id: Mapped[str] = mapped_column(String(120), index=True, default="")
    doc_id: Mapped[str] = mapped_column(String(256), index=True, default="")
    chunk_id: Mapped[str] = mapped_column(String(256), default="")

    # Optional reference to the workflow run that produced the artefact
    run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("workflow_runs.id", ondelete="SET NULL"),
        index=True, default=None,
    )

    # Feedback payload
    category: Mapped[str] = mapped_column(
        String(64), index=True, default="other",
    )  # e.g. "missed_header_strip", "bad_bullet_fix", "ocr_artefact", "other"
    description: Mapped[str] = mapped_column(Text, default="")

    # Optional source span (character offsets) to pinpoint the issue
    source_span_start: Mapped[int | None] = mapped_column(Integer, default=None)
    source_span_end: Mapped[int | None] = mapped_column(Integer, default=None)

    created_by: Mapped[str] = mapped_column(String(256), default="")

    meta: Mapped[dict] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------------------
# VLM ingest — durable session state
#
# VLM ingest was originally an in-memory-only subsystem: the session registry
# was the system of record, and everything else (write-through .md checkpoints,
# activity-based TTL, client-poll keep-alive, localStorage resume) existed to
# compensate for that.  A cache-eviction policy could therefore destroy hours
# of paid VLM output.
#
# These tables make the ledger the system of record instead.  The in-memory
# registry is now a pure hydration cache: evicting it is free, because every
# durable fact lives here.
# ---------------------------------------------------------------------------


class VlmSession(Base):
    """One VLM ingest workflow, durable across restarts and cache eviction."""

    __tablename__ = "vlm_sessions"

    session_id: Mapped[str] = mapped_column(String(32), primary_key=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
    )

    # Optional link to the pipeline run this session was started from.
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="SET NULL"),
        index=True,
        default=None,
    )

    source_filename: Mapped[str] = mapped_column(String(512), default="")
    # Content address of the source PDF — also the cache key prefix for pages.
    source_sha256: Mapped[str] = mapped_column(String(64), default="", index=True)
    # On-disk location of the source PDF, so an evicted session can rehydrate
    # fully (previews, thumbnails and re-processing all need the bytes back).
    source_path: Mapped[str] = mapped_column(Text, default="")

    page_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="configuring", index=True)
    headless: Mapped[bool] = mapped_column(Boolean, default=False)

    config: Mapped[dict] = mapped_column(JSON, default=dict)
    stitched_markdown: Mapped[str] = mapped_column(Text, default="")


class VlmPageResult(Base):
    """Per-page outcome for one session. Written as each page completes."""

    __tablename__ = "vlm_page_results"
    __table_args__ = (UniqueConstraint("session_id", "page_num", name="uq_vlm_page_session_page"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("vlm_sessions.session_id", ondelete="CASCADE"),
        index=True,
    )
    page_num: Mapped[int] = mapped_column(Integer)

    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
    )

    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    markdown: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(160), default="")
    error: Mapped[str] = mapped_column(Text, default="")

    # Which memo entry produced this result (empty for skipped/errored pages).
    cache_key: Mapped[str] = mapped_column(String(64), default="", index=True)


class VlmPageCache(Base):
    """Content-addressed memo of VLM page extractions.

    Keyed on everything that determines the output: source document hash, page
    number, render settings, system prompt and model.  A re-run of the same
    document — after eviction, a crash, or an operator restarting a job — is a
    cache hit rather than a paid model call, so a long job can never lose more
    than the page that was in flight.
    """

    __tablename__ = "vlm_page_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
    )

    # Denormalised for purge-by-document and cache introspection.
    source_sha256: Mapped[str] = mapped_column(String(64), default="", index=True)
    page_num: Mapped[int] = mapped_column(Integer, default=0)

    markdown: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(160), default="")

    meta: Mapped[dict] = mapped_column(JSON, default=dict)
