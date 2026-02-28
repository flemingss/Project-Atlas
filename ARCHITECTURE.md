# Project Atlas - Architecture

## Overview

Project Atlas implements a modular, diagnosable document ingestion + retrieval service with a pipeline scaffold.

Source of truth: `TECHNICAL_DESIGN.md` (current reality, explicit scope decisions, and roadmap). `HLD.md` is historical original intent.

## Core Modules

### Pipeline Module (`atlas.pipeline`)

Pipeline implementing the full agentic flow: **Ingest → Cleanup → Judge → Refine → Metadata → Embeddings → Chunking → Commit** (11 nodes including HITL, COMPLETED, FAILED).

Status note (Feb 2026): `/rag/ingest/*` is pipeline-backed (text + file). All nodes are wired with real provider calls. v0.7.0 adds rich judge-to-refine context injection (sub-scores, rationale, iteration context), per-dimension judge rationale, score regression rollback, diminishing-returns detection, cleanup-rejudge cycle guard, failed-refine-doesn’t-burn-retry semantics, judge error fallback → neutral (score=3), rich HITL task context, HITL resume loop guard (MAX_HITL_RESUMES=2), updated config defaults (cleanup_rejudge=true, formatting/cohesion floors=2, refine_max_retries=3), scope-change cache invalidation in UI, and HITL resume failure feedback. v0.6.0 adds refine content-safety guardrails and runner consolidation. v0.5.0 adds config-driven cleanup rules engine, cleanup feedback capture, metrics aggregation, LLM-assisted rule suggestion, and Cleanup & Tuning UI on top of v0.4.0’s deterministic Cleanup node, multi-dimensional judge rubric, unified routing, retry/backoff, chunk QA with fallback, and Docling health scoring.

- **`ingest.py`** - Document ingestion node
  - Scaffold for ingest orchestration
  - Docling-based parsing is supported as an optional dependency (best-effort; see `TECHNICAL_DESIGN.md` Phase 4)
  - **Layout PDF parser** (v0.7.2): ONNX-based layout-aware PDF pipeline derived from RAGFlow deepdoc (Apache 2.0). Selected via `atlas_pdf_parser_backend` setting (`auto`/`layout`/`docling`). Default `auto` tries layout parser first, falls back to Docling on failure or low OCR confidence.

### Ingest Subsystem (`atlas.ingest`)

Layout-aware PDF parsing pipeline ported from RAGFlow's deepdoc engine:

- **`types.py`** — Shared type definitions: `LayoutType` enum (10 types), `LayoutBox`, `OCRBox`, `ParsedRegion`, `TableResult`, `PDFParseResult` dataclasses, `GARBAGE_LAYOUT_TYPES` frozenset
- **`model_manager.py`** — Thread-safe singleton for ONNX model download/caching from HuggingFace `InfiniFlow/deepdoc`. 5 required models: `layout.onnx`, `det.onnx`, `rec.onnx`, `ocr.res`, `tsr.onnx`
- **`layout_recognizer.py`** — Page layout recognition via ONNX inference. Auto-detects PaddleDetection vs YOLOv10 model format. Includes NMS, OCR-box tagging, noise filtering, and geometry helpers
- **`postprocess.py`** — OCR post-processing: `DBPostProcess` (Differentiable Binarization text detection), `CTCLabelDecode` (CTC text recognition decoding)
- **`ocr.py`** — ONNX-based OCR: `TextDetector` (DBNet, 960px max), `TextRecognizer` (CRNN batch=16), `OCR` facade combining detection + recognition with rotation-aware cropping
- **`table_recognizer.py`** — Table structure recognition from `tsr.onnx`. HTML construction, row/column alignment, caption detection, colspan/rowspan support
- **`text_extractor.py`** — Hybrid text extraction merging pdfplumber programmatic chars with OCR. Multi-column detection via KMeans clustering
- **`pdf_parser.py`** — Main `LayoutPdfParser` entry point: 7-step pipeline (page render → hybrid OCR → layout → table → text merge → reading order → markdown). Produces `PDFParseResult` with confidence metrics

- **`cleanup.py`** - Deterministic markdown cleanup node
  - Five built-in transforms plus five configurable builtin extraction-artifact fixes (`html_unescape`, `fix_ligatures`, `strip_zero_width_chars`, `strip_page_numbers`, `strip_repetitive_lines` — first three ON by default, last two OFF by default)
  - Runs between Ingest and Judge; no LLM calls
  - Accepts optional `doc_context` and `config` to apply config-driven cleanup rules after built-in transforms
  - Produces `CleanupResult` with per-transform change flags + rule-engine fields (`rules_applied`, `rules_failed`, `fix_counts`, `rule_tags`)

- **`cleanup_rules.py`** - Config-driven cleanup rules engine
  - Declarative, first-match-wins rule resolution based on tenant_id, project_id, corpus_id, mime_type, filename_pattern
  - Seven step handlers: `strip_lines_matching`, `rewrite_pattern`, `strip_headers_footers`, `normalize_headings`, `merge_hardwrapped_paragraphs`, `fix_bullets`, `html_unescape`
  - Rule tags (`hard_failure`, `suspicious_content`, `auto_fix_only`) influence routing decisions
  - Rules configured in `pipeline.yaml` `cleanup_rules:` section

- **`judge.py`** - Multi-dimensional quality grading node
  - Four-dimension rubric: FAITHFULNESS, FORMATTING, COHESION, HALLUCINATION_RISK (each 1–5)
  - Composite score = rounded mean of sub-scores
  - Per-dimension rationale for scores below 4 (specific issues + improvement guidance)
  - Four few-shot examples including a mixed-score example (faithfulness=5, formatting=2)
  - Error fallback: `score=3` / `needs_refinement=False` (neutral — transient LLM failures don’t burn refine retries)
  - Legacy single-SCORE fallback preserved for backward compatibility
  - Versioned `judge_version` (model + prompt hash)

- **`refine.py`** - Document refinement node
  - Receives rich judge context: per-dimension sub-scores (with “← focus here” markers for low dimensions), rationale, and iteration context (“Attempt X of Y”)
  - Content-safety guardrails: tightened system prompt (“MUST NOT summarise, condense, or omit”), `min_preservation_ratio` (default 0.6) rejects outputs shorter than 60% of input
  - Versioned `refine_version` (v2) with prompt hash tracking
  - Configurable max retries → HITL escalation when exhausted; only successful refinements count against the retry limit

- **`metadata.py`** - Tiered metadata generation
  - Scaffold for tiered metadata generation

- **`routing.py`** - Unified routing logic
  - `decide_next_step()` pure function returns a frozen `RoutingDecision` (includes `rollback: bool` field)
  - Supports fail-fast (composite ≤ `fail_fast_score`), cleanup-rejudge (max 1 cycle), per-dimension floor checks, standard refine/HITL paths
  - **Score regression rollback**: if refine makes the score worse, routes to metadata (rollback=True, pre-refine score ≥ cutoff) or HITL (rollback=True, pre-refine score < cutoff)
  - **Diminishing-returns detection**: if score is unchanged after refine, escalates to HITL
  - **Cleanup-rejudge cycle guard**: `cleanup_rejudge_count` capped at 1 to prevent infinite loops
  - `content_ok` check includes `hallucination_risk` alongside `faithfulness` and `cohesion`
  - Rule-tag-aware cleanup routing: `hard_failure` → FAILED, `suspicious_content` → HITL, other tags → standard cleanup→judge
  - All branching logic centralised here; callers never inspect scores directly

- **`orchestrator.py`** - Pipeline coordination
  - Manages state transitions between nodes
  - Dispatches to cleanup, judge, refine, metadata, and commit nodes
  - Integrates with diagnostics for traceability

- **`state.py`** - State management
  - Defines 11 pipeline nodes (INGEST, CLEANUP, JUDGE, REFINE, METADATA, EMBEDDINGS, CHUNKING, COMMIT, HITL, COMPLETED, FAILED) and valid transitions
  - CLEANUP transitions: → JUDGE, → HITL (via `suspicious_content` rule tag), → FAILED (via `hard_failure` rule tag)
  - JUDGE transitions: → REFINE, → METADATA, → HITL, → FAILED, → CLEANUP (cleanup-rejudge)
  - Tracks `judge_score_history` (list of all scores), `pre_refine_markdown` (for rollback), `cleanup_rejudge_count`
  - `set_refine_result()`: only increments `refine_retries` on success; tracks `refine_total_attempts` with 2× circuit-breaker hard cap
  - `get_next_node()` delegates to `routing.decide_next_step()` and performs markdown rollback when `decision.rollback` is True

### Data Models (`atlas.schemas`)

Comprehensive data structures with full traceability:

- **`ChunkMetadata`** - Enhanced chunk metadata
  - Hierarchical structure: parent_header_id, sibling_ids, section_path
  - Quality metrics: judge_score, fidelity_flag, confidence_rationale
  - Traceability: embedding_version, judge_version, parse_profile
  - Metadata tiers: tier 1 (local) vs tier 2 (frontier)

- **`DocumentIngestState`** - Pipeline state
  - Current node and processing status
  - Quality metrics (mean_judge_score, chunks_finalized, etc.)
  - Error tracking with structured codes
  - Docling JSON ground truth storage

- **Pipeline Results** - Structured outputs
  - `JudgeResult`: composite score, sub_scores (per-dimension dict), rationale (per-dimension for scores <4), version, refinement decision
  - `RefineResult`: refined markdown, improvements made, success flag
  - `MetadataResult`: tags, tier used, model info
  - `CleanupResult`: cleaned markdown, per-transform flags, changes_made boolean

- **`HITLTask`** - Human-in-the-Loop tasks
  - Priority calculation: (10 - judge_score) * sensitivity_multiplier
  - Before/after markdown for review
  - Status tracking and assignment

### Diagnostics Module (`atlas.diagnostics`)

Structured error handling and performance tracking:

- **Error Codes** - Structured enum for all error types
  - DOC_PARSE_TIMEOUT, VLM_OCR_FAIL, JUDGE_INVALID_SCORE, etc.
  - Enables precise error tracking and debugging

- **Trace Levels** - Configurable logging depth
  - NONE, BASIC, DETAILED, FULL
  - FULL captures intermediate prompts/responses

- **Performance Metrics**
  - Operation duration tracking
  - Success/failure rates
  - Context manager for automatic metrics: `with diagnostics.trace_operation(...)`

- **Event Logging**
  - Structured events with timestamp, component, context
  - Separate channels for errors, warnings, info
  - Aggregated summaries for monitoring

### Concurrency Management (`atlas.concurrency`)

Resource management and task coordination:

- **ConcurrencyGuard** - Heavy task limiting
  - Semaphore for vLLM with concurrency = 1
  - Queue depth tracking
  - Automatic metric logging

- **Resource Monitoring**
  - VRAM threshold checking (default: 92%)
  - Queue depth threshold (default: 2)
  - Frontier fallback decision logic

- **Privacy Guard**
  - Default is_sensitive: true
  - API routing requires explicit override
  - Enforced per-tenant

### HITL Management (`atlas.hitl`)

Human-in-the-Loop workflow:

- **Priority Queue**
  - Formula: (10 - judge_score) * sensitivity_multiplier
  - High-sensitivity + Low Score = Top priority
  - Automatic sorting on task creation

- **Task Management**
  - Create, assign, complete, skip operations
  - Before/after markdown tracking
  - Reason for edit documentation

- **Integration surface** (scaffold)
  - Current: Postgres-backed HITL tasks + admin endpoints under `/admin/hitl/*`
  - In-memory queue remains present but is no longer the primary runtime path
  - Dify integration remains optional/experimental
  - Primary direction is a purpose-built console (“Control Center”) per `TECHNICAL_DESIGN.md`

### Retry / Backoff (`atlas.retry`)

Config-driven retry with exponential backoff for all external calls:

- **`RetryConfig`** dataclass with `max_retries`, `base_delay_s`, `max_delay_s`
- **`async_retry()`** / **`sync_retry()`** decorators wrapping provider calls
- Config loaded from `pipeline.yaml` `retry:` section, keyed by subsystem (`llm`, `vectorstore`, `docling`)
- Applied to: `openai_compat.py` (chat/embed), `qdrant_store.py` (upsert/delete), `docling_adapter.py` (convert)

### Chunk QA + Fallback (`atlas.rag.chunk_qa`)

Post-chunking quality validation with automatic fallback:

- **`validate_chunks()`** — checks min/max token bounds and minimum chunk count
- **`chunk_with_fallback()`** — tries the configured strategy; on QA failure falls back (semantic→paragraph, hierarchical→paragraph)
- Configurable bounds via `pipeline.yaml` `chunking.qa:` section

### Docling Health Score (`atlas.ingest.docling_health`)

Quantitative ingest quality signal computed after every document parse:

- **`compute_health()`** — scores extraction_method, content_volume, rotation, text_as_shapes signals
- Produces a composite 1–5 `health_score` stored on `PipelineContext`
- Low scores surface in Looking Glass and can inform operator triage

### Cleanup Feedback & Metrics (`atlas.feedback_ledger`)

Operator feedback loop for cleanup quality:

- **`CleanupFeedback`** model (Postgres) — scoped by tenant/project/corpus/doc/chunk
- **CRUD helpers** — `create_feedback`, `get_feedback`, `list_feedback`, `delete_feedback`, `feedback_category_counts`
- **Five API endpoints** under `/admin/cleanup-feedback` — create, list (scoped), categories (aggregation), get by ID, delete
- **Metrics aggregation endpoint** (`GET /admin/looking-glass/metrics`) — workflow status distribution, node failure rates, HITL escalation rates, auto-accepted counts, cleanup-feedback category counts (scoped by tenant/project/corpus)

### Rule Suggester (`atlas.rule_suggester`)

LLM-assisted cleanup rule suggestion (Phase 7D):

- **`suggest_cleanup_rule()`** — accepts sample markdown + issues + optional context, calls the configured LLM, returns `{rule_yaml, rationale}`
- **Heuristic fallback** — `_heuristic_suggestion()` detects hard-wrapped paragraphs, mixed bullets, setext headings, header/footer keywords, OCR artifacts
- **Deterministic provider branch** — `DeterministicProvider._suggest_rule_json()` returns stable suggestion JSON for CI/test
- **Endpoint**: `POST /admin/cleanup-rules/suggest` (resolves `chat_model` → `refine_model` fallback)

### Cleanup & Tuning UI Card

Streamlit Admin tab card (Phase 7E) for operator self-service:

- **Active rules display** — fetches effective config and lists all cleanup rules with expandable JSON
- **Feedback submission** — form for document ID, category, and comment → `POST /admin/cleanup-feedback`
- **Feedback overview** — category histogram from `GET /admin/cleanup-feedback/categories`
- **Pipeline metrics** — summary dashboard from `GET /admin/looking-glass/metrics`
- **AI rule suggestion** — paste markdown + describe issues → calls `POST /admin/cleanup-rules/suggest` → inline YAML preview

### Normalize (`atlas.rag.normalize`)

Formatting-only markdown normalization applied before chunking:

- **Formatting cleanup** — collapses excessive blank lines, normalises line breaks
- **No content removal** — page-number stripping and noise removal moved to `cleanup.py` builtins (`strip_page_numbers`, `strip_repetitive_lines`)
- **Tracked as a pipeline node run** for auditability

### Runner Consolidation (`atlas.pipeline.runner`)

Unified pipeline runner (~996 lines) with shared helpers:

- **`_record_pipeline_node_runs()`** — writes node-run rows to the workflow ledger
- **`_record_normalize_node_run()`** — records normalize as a tracked pipeline step
- **`_persist_markdown_artifact()`** — writes markdown artifacts to the filesystem store
- **`_handle_hitl_pause()`** — creates HITL tasks with rich context (judge sub-scores, rationale, score history, refine attempts, last improvements) and pauses the pipeline
- **`_commit_chunks_to_qdrant()`** — chunks, embeds, and upserts to Qdrant
- **HITL resume guard** — `MAX_HITL_RESUMES=2` prevents infinite HITL→pipeline→HITL loops; resume count tracked in `WorkflowRun.meta["hitl_resume_count"]`
- **`max_refine_retries`** read from `limits` section (with backwards-compat fallback to `thresholds`)
- All silent `except: pass` blocks replaced with `log.warning` for observability

### Enhanced Chunking (`atlas.rag.chunking`)

Three chunking strategies available (configurable via `pipeline.yaml`). Default is `semantic` (token-aware markdown-heading chunker). All chunking sites use `chunk_with_fallback()` for automatic QA + fallback.

- **Hierarchical Structure**
  - Extract markdown headings (# through ######)
  - Build section hierarchy
  - Track section_path: ["Chapter 1", "Thermal Dynamics"]

- **Relationships**
  - parent_header_id: Links to parent section
  - sibling_ids: Related chunks in same section
  - Enables context-aware retrieval

- **Smart Splitting**
  - Respect heading boundaries
  - Preserve section context
  - Handle oversized paragraphs

## Design Principles

### Modularity
- Clear separation of concerns
- Each module has single responsibility
- Pluggable components (e.g., swap LLM providers)

### Traceability
- Every chunk stores: judge_version, embedding_version, parse_profile
- Pipeline state fully tracked
- Structured error codes with context

### Diagnosability
- Comprehensive logging at multiple levels
- Performance metrics for all operations
- Structured events for monitoring

### Best Practices
- Type hints throughout
- Dataclasses for immutable data
- Context managers for resource cleanup
- Comprehensive docstrings
- Unit tests for all modules

## Testing

Current automated coverage (**358 tests passing**, 0 failures):
- Schema creation and validation (incl. `CleanupResult`, `JudgeResult.sub_scores`)
- Diagnostics and error handling
- Pipeline state transitions (11 nodes)
- HITL priority calculations
- Admin and RAG endpoints (incl. fidelity mode search filter)
- Config management
- Qdrant integration
- Retry/backoff (`test_retry.py` — 14 tests)
- Chunk QA + fallback (`test_chunk_qa.py` — 9 tests)
- Cleanup node (`test_cleanup.py` — 15 tests)
- Docling health score (`test_docling_health.py` — 15 tests)
- Routing logic (`test_routing.py` — 21 tests)
- **Cleanup rules engine** (`test_cleanup_rules.py` — 34 tests)
- **Cleanup feedback API** (`test_cleanup_feedback.py` — 7 tests)
- **Metrics aggregation** (`test_metrics_aggregation.py` — 3 tests)
- **Rule suggestion** (`test_rule_suggestion.py` — 13 tests)
- **Phase refactors** (`test_phase_refactors.py` — 40 tests): refine guardrails, normalize boundary, runner consolidation, html_unescape dedup
- **Cleanup rules import/export** (`test_cleanup_rules_import_export.py` — 10 tests): export YAML, import replace/merge, validation, round-trip

Run tests:
```bash
pytest -q                    # All tests (358 passing)
pytest -m integration        # Integration tests only
```

## Next Steps

The current implementation (v0.5.0) provides:
- ✅ All core data models (incl. multi-dimensional judge, cleanup results, cleanup feedback)
- ✅ Full 11-node pipeline (Ingest → Cleanup → Judge → Refine → Metadata → Embeddings → Chunking → Commit + HITL/COMPLETED/FAILED)
- ✅ Config-driven cleanup rules engine (7 step types, first-match-wins, rule-tag routing)
- ✅ Cleanup feedback capture + metrics aggregation API
- ✅ LLM-assisted rule suggestion (`POST /admin/cleanup-rules/suggest`)
- ✅ Cleanup & Tuning UI card in Admin tab
- ✅ Retry/backoff on all external calls
- ✅ Chunk QA with automatic fallback
- ✅ Docling integration with health scoring
- ✅ Unified routing with fail-fast, floor checks, cleanup-rejudge, rule-tag escalation
- ✅ Diagnostics and concurrency management
- ✅ HITL workflow
- ✅ Enhanced chunking (3 strategies + QA)
- ✅ Fidelity mode search filter

All Phase 7 items (7A–7E) are complete.
- ✅ Refine content-safety guardrails (min_preservation_ratio, tightened prompt, refine_version v2)
- ✅ Normalize refactored to formatting-only (whitespace/line-break normalization); page-number and noise stripping moved to cleanup builtins
- ✅ Runner consolidation (5 shared helpers, 37% line reduction)
- 🔄 Prompt/rubric tuning as real-world usage data accumulates
- 🔄 Retrieval upgrades (hybrid/rerank) are explicitly deferred unless a measured failure mode requires them
- 🔄 Semantic cache implementation
- 🔄 Purpose-built Control Center console (replaces Streamlit for full HITL + monitoring)
