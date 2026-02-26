"""Enhanced data models for Project Atlas RAG pipeline.

This module defines comprehensive data structures for document processing,
chunk metadata, and pipeline state management following the HLD requirements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FidelityFlag(str, Enum):
    """Quality flags for chunk fidelity."""

    VERIFIED = "verified"
    PARTIAL = "partial"
    LOW_CONFIDENCE = "low_confidence"
    NEEDS_REVIEW = "needs_review"


class ParseProfile(str, Enum):
    """Document parsing profiles."""

    PDF_SCANNED = "pdf_scanned"
    PDF_TEXT = "pdf_text"
    PPTX = "pptx"
    DOCX = "docx"
    MARKDOWN = "markdown"
    TEXT = "text"


@dataclass
class ChunkMetadata:
    """Comprehensive metadata for a document chunk following HLD specifications.

    Includes traceability, quality metrics, hierarchical structure,
    and embedding information for professional-grade RAG.
    """

    # Core identifiers
    tenant_id: str
    project_id: str
    doc_id: str
    doc_version: str
    chunk_index: int

    # Content and hashing
    text: str
    content_hash: str

    # Hierarchical structure (HLD: section 2)
    parent_header_id: str | None = None
    sibling_ids: list[str] = field(default_factory=list)
    section_path: list[str] = field(default_factory=list)

    # Quality and fidelity (HLD: Judge/Refine nodes)
    judge_score: float | None = None
    judge_version: str | None = None
    confidence_rationale: str | None = None
    fidelity_flag: FidelityFlag | None = None
    refine_count: int = 0

    # Traceability (HLD: Ingest node)
    source_mime_type: str = "text/plain"
    parse_profile: ParseProfile | None = None
    docling_schema_version: str | None = None

    # Embedding information (HLD: Embeddings Module)
    embedding_model: str | None = None
    embedding_version: str | None = None
    embedding_provider: str | None = None
    embedding_params: dict[str, Any] = field(default_factory=dict)

    # Metadata tiers (HLD: section 2, Metadata node)
    metadata_tier: int = 1  # 1 for local small model, 2 for frontier/70B
    metadata_tags: dict[str, Any] = field(default_factory=dict)

    # State management (HLD: Commit node)
    is_finalized: bool = False
    is_sensitive: bool = True
    supersedes_chunk_id: str | None = None

    # Timestamps
    created_at: str | None = None
    updated_at: str | None = None

    # Additional metadata
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentIngestState:
    """State for a document in the agentic processing pipeline (HLD section 2)."""

    doc_id: str
    doc_version: str
    tenant_id: str
    project_id: str
    source_mime_type: str

    # Source / corpus information
    corpus_id: str = ""
    source_uri: str | None = None
    parse_profile: ParseProfile | None = None

    # Processing state
    current_node: str = "ingest"  # ingest, judge, refine, metadata, embeddings, commit
    refine_retries: int = 0
    max_refine_retries: int = 2

    # Quality metrics
    mean_judge_score: float | None = None
    chunks_total: int = 0
    chunks_finalized: int = 0
    chunks_needs_hitl: int = 0
    tier2_chunks_used: int = 0

    # Flags
    is_completed: bool = False
    needs_hitl: bool = False

    # Timestamps
    started_at: str | None = None
    completed_at: str | None = None

    # Raw docling output (HLD: ground truth)
    docling_json: dict[str, Any] = field(default_factory=dict)
    markdown_projection: str = ""

    # Error tracking
    error_code: str | None = None
    error_message: str | None = None


@dataclass
class JudgeResult:
    """Result from the Judge node (HLD section 2).

    ``sub_scores`` holds per-dimension grades (faithfulness, formatting,
    cohesion, hallucination_risk) each 1-5.  The composite ``score`` is the
    rounded mean of sub_scores when available, or a single overall score for
    backwards compatibility with older judge prompts.
    """

    score: int  # 1-5 composite (rounded mean of sub_scores)
    confidence_rationale: str
    judge_version: str
    needs_refinement: bool
    timestamp: str
    sub_scores: dict[str, int] = field(default_factory=dict)


@dataclass
class CleanupResult:
    """Result from the Cleanup node — deterministic markdown transforms.

    The built-in transforms populate ``transforms_applied`` and ``warnings``.
    If a config-driven cleanup rule matched, the rule-engine fields are also
    populated:
    - ``rules_applied``: name(s) of rules whose steps changed the markdown.
    - ``rules_failed``:  name(s) of rules that encountered errors.
    - ``fix_counts``:    per-step fix count from the matched rule.
    - ``rule_tags``:     tags from the matched rule (consumed by routing).
    """

    cleaned_markdown: str
    transforms_applied: list[str]
    warnings: list[str]
    chars_before: int
    chars_after: int
    timestamp: str

    # Config-driven rule-engine fields (Phase 7A)
    rules_applied: list[str] = field(default_factory=list)
    rules_failed: list[str] = field(default_factory=list)
    fix_counts: dict[str, int] = field(default_factory=dict)
    rule_tags: list[str] = field(default_factory=list)


@dataclass
class RefineResult:
    """Result from the Refine node (HLD section 2)."""

    refined_markdown: str
    improvements_made: list[str]
    refine_version: str
    success: bool
    timestamp: str


@dataclass
class MetadataResult:
    """Result from metadata generation (HLD section 2, Metadata node)."""

    tags: dict[str, Any]
    tier: int  # 1 or 2
    model_used: str
    timestamp: str


@dataclass
class HITLTask:
    """Human-in-the-Loop task (HLD section 5)."""

    task_id: str
    doc_id: str
    chunk_id: str
    tenant_id: str
    project_id: str

    # Priority factors (HLD: High-sensitivity + Low Score = Top)
    priority_score: float
    is_sensitive: bool
    judge_score: float

    # Content for review
    before_md: str
    created_at: str

    # Optional fields with defaults
    after_md: str = ""
    reason_for_edit: str = ""

    # State
    status: str = "pending"  # pending, in_progress, completed, skipped
    assigned_to: str | None = None

    # Timestamps
    completed_at: str | None = None


@dataclass
class RAGManifest:
    """Manifest for a processed RAG package (HLD section 6)."""

    doc_id: str
    doc_version: str
    origin_uri: str
    ingest_time: str

    # Quality metrics
    mean_judge_score: float
    chunks_total: int
    chunks_hitl_percent: float

    # Model information
    embedding_model: str
    judge_model: str
    metadata_model: str

    # Configuration snapshot
    pipeline_config_version: str

    # Health summary
    health_summary: dict[str, Any] = field(default_factory=dict)
