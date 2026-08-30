# Project Atlas - Architecture

## Overview

Project Atlas implements a modular, diagnosable document ingestion + retrieval service with a fully wired agentic pipeline.

Source of truth: `TECHNICAL_DESIGN.md` (current reality, explicit scope decisions, and roadmap).

## Core Modules

### Pipeline Module (`atlas.pipeline`)

Pipeline implementing the full agentic flow: **Ingest → Cleanup → Judge → Refine → Metadata → Embeddings → Chunking → Commit** (11 nodes including HITL, COMPLETED, FAILED).

Status note (Mar 2026): `/rag/ingest/*` is pipeline-backed (text + file). All nodes wired with real provider calls. See `CHANGELOG.md` for version-by-version feature details.

- **`ingest.py`** - Document ingestion node
  - `IngestNode` resolves a parser strategy and normalises every backend into a single `IngestResult`
  - Docling-based parsing is supported as an optional dependency (best-effort; see `TECHNICAL_DESIGN.md` Phase 4)
  - **Layout PDF parser** (v0.7.2): ONNX-based layout-aware PDF pipeline derived from RAGFlow deepdoc (Apache 2.0). Selected via `pipeline.yaml` `pdf_parser.backend` (falling back to the `atlas_pdf_parser_backend` setting). Strategies are built in `pipeline/parsers.py` — five backends: `auto` (Docling first → layout fallback), `auto_layout` (layout first → Docling fallback), `vision` (VLM per-page), `layout` (layout only), `docling` (Docling only). Selection is whole-document, not per-page.

### Ingest Subsystem (`atlas.ingest`)

Layout-aware PDF parsing pipeline ported from RAGFlow's deepdoc engine:

- **`types.py`** — Shared type definitions: `LayoutType` enum (10 types), `ParsedRegion`, `TableResult`, `PDFParseResult` dataclasses, `GARBAGE_LAYOUT_TYPES` frozenset
- **`model_manager.py`** — Thread-safe singleton for ONNX model download/caching from HuggingFace `InfiniFlow/deepdoc`. 5 required models: `layout.onnx`, `det.onnx`, `rec.onnx`, `ocr.res`, `tsr.onnx`
- **`layout_recognizer.py`** — Page layout recognition via ONNX inference. Auto-detects PaddleDetection vs YOLOv10 model format. Includes NMS, OCR-box tagging, noise filtering, and geometry helpers
- **`postprocess.py`** — OCR post-processing: `DBPostProcess` (Differentiable Binarization text detection), `CTCLabelDecode` (CTC text recognition decoding)
- **`ocr.py`** — ONNX-based OCR: `TextDetector` (DBNet, 960px max), `TextRecognizer` (CRNN batch=16), `OCR` facade combining detection + recognition with rotation-aware cropping
- **`table_recognizer.py`** — Table structure recognition from `tsr.onnx`. HTML construction, row/column alignment, caption detection, colspan/rowspan support
- **`text_extractor.py`** — Hybrid text extraction merging pdfplumber programmatic chars with OCR. Multi-column detection via KMeans clustering
- **`pdf_parser.py`** — Main `LayoutPdfParser` entry point: 7-step pipeline (page render → hybrid OCR → layout → table → text merge → reading order → markdown). Produces `PDFParseResult` with confidence metrics

- **`cleanup.py`** - Deterministic markdown cleanup node
  - Five built-in transforms plus nine configurable builtin extraction-artifact fixes (`html_unescape`, `fix_ligatures`, `strip_zero_width_chars`, `strip_bullet_glyphs`, `strip_page_numbers`, `normalize_superscripts`, `dedupe_table_spans`, `strip_repetitive_lines`, `strip_repeated_headings`) — seven are ON by default; `strip_repetitive_lines` and `strip_repeated_headings` default to OFF because they remove content
  - `strip_page_numbers` is skipped when the document was parsed with `parse_profile == "pdf_layout"` (the layout parser already removes them)
  - Runs between Ingest and Judge; no LLM calls
  - Accepts optional `doc_context` and `config` to apply config-driven cleanup rules after built-in transforms
  - Produces `CleanupResult` with per-transform change flags + rule-engine fields (`rules_applied`, `rules_failed`, `fix_counts`, `rule_tags`)

- **`cleanup_rules.py`** - Config-driven cleanup rules engine
  - Declarative, first-match-wins rule resolution based on tenant_id, project_id, corpus_id, mime_type, filename_pattern
  - Eight step handlers (`_STEP_REGISTRY`): `strip_lines_matching`, `rewrite_pattern`, `strip_headers_footers`, `normalize_headings`, `fix_numbered_headings`, `merge_hardwrapped_paragraphs`, `fix_bullets`, `html_unescape`
  - Rule tags (`hard_failure`, `suspicious_content`, `auto_fix_only`) influence routing decisions
  - Rules configured in `pipeline.yaml` `cleanup_rules:` section. The stock `config/pipeline.yaml` ships `cleanup_rules: []` with commented examples; the ~10 reference rules live in `personal_configs/`

- **`judge.py`** - Multi-dimensional quality grading node
  - Four-dimension rubric: FAITHFULNESS, FORMATTING, COHESION, HALLUCINATION_RISK (each 1–5)
  - Composite score = rounded mean of sub-scores
  - Per-dimension rationale for scores below 4 (specific issues + improvement guidance)
  - Four few-shot examples including a mixed-score example (faithfulness=5, formatting=2)
  - Error fallback: `score=3` / `needs_refinement=False` (neutral — transient LLM failures don’t burn refine retries)
  - Legacy single-SCORE fallback preserved for backward compatibility
  - Versioned `judge_version` (model + prompt hash)
  - **Oversize guard**: the judge prompt embeds the whole document with no truncation, so documents estimated above `limits.judge_max_context_tokens` **skip** grading instead of failing ingest with an over-length request. They are still cleaned, chunked, embedded and searchable — just not quality-gated or refined (recorded as `judge_version=skipped-oversize:<model>`)

- **`refine.py`** - Document refinement node
  - Receives rich judge context: per-dimension sub-scores (with “← focus here” markers for low dimensions), rationale, and iteration context (“Attempt X of Y”)
  - Content-safety guardrails: tightened system prompt ("MUST NOT summarise, condense, or omit"), `min_preservation_ratio` (default 0.85) rejects outputs shorter than 85% of input
  - **Sectional refinement** (v0.7.1): `tokens.fits_in_context()` enforces two ceilings — the context budget (`limits.max_context_tokens`) *and* the refine model's `max_output_tokens`. Documents failing either are split on headings (`limits.refine_max_section_tokens`) and refined section-by-section, then reassembled
  - **Section-count preservation guard**: rejects outputs that drop ≥20% of input headings (min 3 headings to trigger)
  - **LLM artifact stripping** (`strip_llm_artifacts()`, now in `pipeline/guardrails.py`): deterministic removal of `<think>` tags, preamble/postamble, code fences, meta-commentary
  - Dynamic `max_tokens` per call: `max(512, int(input_est * 1.15))`
  - Versioned `refine_version` (v2) with prompt hash tracking
  - Configurable max retries → HITL escalation when exhausted; only successful refinements count against the retry limit

- **`metadata.py`** - Tiered metadata generation
  - Fully implemented and live on the runtime path
  - Runs **once per document**, not per chunk, and only sees `content[:1000]`
  - Tier 1 (`metadata_tier1_model`) is the default; tier 2 (`metadata_tier2_model`) is selected for borderline judge scores (3–4), capped by `limits.tier2_chunk_cap_per_document`
  - The single resulting tag set is copied onto every chunk payload committed to Qdrant

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

- **`FidelityFlag` / `ParseProfile`** - Enums shared by chunk payloads and diagnostics
  - There is no `ChunkMetadata` dataclass; the runner assembles Qdrant payloads directly
  - Payload fields cover hierarchy (`parent_header_id`, `sibling_ids`, `section_path`), quality (`judge_score`, `fidelity_flag`, `confidence_rationale`) and traceability (`embedding_version`, `judge_version`, `parse_profile`)

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
  - Cross-run aggregation is served by `GET /admin/looking-glass/metrics`, not by the in-process diagnostics manager

### HITL Management (`atlas.hitl_ledger` + `atlas.admin.hitl`)

Human-in-the-Loop workflow, durably backed by Postgres (`hitl_tasks`):

- **Priority Queue**
  - `compute_priority_score()`: `(10 - judge_score) * sensitivity_multiplier` (`is_sensitive` → 2.0, otherwise 1.0)
  - High-sensitivity + Low Score = Top priority
  - Queue listings order by `priority_score` descending, then task id

- **Task Management**
  - Create, claim, complete, skip/reject operations
  - Before/after markdown tracking
  - Reason for edit documentation
  - Rich context stored on each task: judge sub-scores, rationale, score history, refine attempts, last improvements

- **Integration surface**
  - Postgres-backed HITL tasks + admin endpoints under `/admin/hitl/*` are the only runtime path; there is no in-memory queue
  - Dify integration remains optional/experimental (behind the `dify` compose profile)
  - Operator surface: **React SPA** (`web/`) built with Vite + TypeScript + shadcn/ui, served at `/app`. Review queue at `/app/review`; the **Document Editor** (PDF.js viewer, CodeMirror 6 markdown editor, VLM tool palette, React Query mutations, Zustand state) opens at `/app/doc/{docId}` or `/app/run/{runId}`. See `web/README.md` for full developer guide.

### VLM Ingest (`atlas.vlm_ingest` + `api_vlm_ingest`)

Vision-language-model-first PDF ingestion — interactive wizard + headless pipeline mode:

- **`stitcher.py`** — Deterministic page-level markdown assembler: page comment insertion, duplicate header/footer removal, table continuation merge, heading dedup
- **`session.py`** — In-memory session registry with TTL, per-page config overrides (DPI, crop), serializable config for headless reuse
- **`api_vlm_ingest.py`** — 16-endpoint FastAPI router at `/api/editor/vlm-ingest`: start session (run ID or upload), list/get/delete sessions, configure globals + per-page overrides, export config, page analysis, thumbnails, preview (with crop overlay params), process page, process all, stitch, commit, get/update a per-page result. There is **no** import route — config import is client-side
- **React wizard** (`web/src/pages/ingest/ingest-page.tsx`, route `/app/ingest`): unified 6-step ingest wizard covering the text, Docling and VLM methods (method + upload → configure → pages → process → review/stitch → commit). Features: PDF preview with red crop guide overlays and a mask editor, Fit Page/Width/Actual zoom modes, auto-advance processing, per-page markdown correction, config export for headless reuse. The separate `web/src/pages/vlm-ingest/` and `web/src/pages/upload/` pages have been removed; `/app/vlm-ingest` and `/app/upload` redirect to `/app/ingest`
- **Session-expired recovery**: Red banner UI when backend session is lost (404). Zustand state + `isSessionNotFoundError()` helper detect session loss across all mutation hooks
- **Headless mode**: `VisionParser` in `pipeline/parsers.py` (`backend: vision`) — per-page render + VLM call, deterministic stitch, config from `pipeline.yaml → pdf_parser.vlm`

### LLM Providers & Profiles (`atlas.llm`)

Generation and embeddings are resolved through `ModelRegistry` from `config/models.yaml`:

- **`provider.py`** — `ILlmProvider` protocol (`chat()` / `embed()`) plus multimodal `ChatMessage` (`content: str | list[dict]`)
- **`openai_compat.py`** — OpenAI-compatible HTTP provider backing every real backend (LM Studio, OpenRouter, the embeddings sidecar). Strips closed *and* unclosed `<think>` blocks, logs `finish_reason`, and raises on `length` truncation
- **`deterministic.py`** — stub provider for CI/E2E: stable SHA-256-derived embeddings + heuristic chat responses
- **`profiles.py`** — an **LLM profile** is a named patch applied over **both** `models.yaml` and `pipeline.yaml`, so model ids and the tuning that must move with them (context budget, section size) switch together. Selected by `ATLAS_LLM_PROFILE`, else `active_profile` in `models.yaml`:
  - `local` — generation on LM Studio (`max_context_tokens: 16384`, `refine_max_section_tokens: 6000`, `judge_max_context_tokens: 32000`)
  - `api` — generation on **OpenRouter** (the shipped default): larger context budget, larger refine sections, `judge_max_context_tokens: 1310720`
- **Zero-data-retention** — the `openrouter` provider declares `enforce_zdr: true`, which adds `provider: {zdr: true}` to every request body so routing is restricted to ZDR-compliant endpoints. This is the privacy control: there is **no** sensitivity-based provider-routing block. `is_sensitive` remains a per-document flag (Qdrant payloads, HITL priority, exports) but does not influence which provider is used
- **Embeddings are pinned across profiles** — `embed_model` is in `PROFILE_IMMUTABLE_ROLES`; a profile that tries to override it raises at load, because re-embedding an existing corpus with a different model corrupts retrieval silently when dimensions happen to match. Embeddings are served by the CPU **`embeddings` sidecar** in docker compose (`http://embeddings:80` on the compose network, host port `18090`). The Atlas API is on host port `28080`

### Retry / Backoff (`atlas.retry`)

Config-driven retry with exponential backoff for all external calls:

- **`RetryConfig`** dataclass with `max_retries`, `base_delay_s`, `max_delay_s`
- **`async_retry()`** / **`sync_retry()`** decorators wrapping provider calls
- Config loaded from `pipeline.yaml` `retry:` section, keyed by subsystem (`llm`, `vectorstore`, `docling`)
- Applied to: `openai_compat.py` (chat/embed), `qdrant_store.py` (upsert/delete), `docling_adapter.py` (convert)

### Chunk QA + Fallback (`atlas.rag.chunk_qa`)

Post-chunking quality validation with automatic fallback:

- **`validate_chunks()`** — gates on `min_chunk_count`, `max_token_ratio_limit`, `max_duplication_ratio` and `min_coverage_ratio`. Min/max/average token counts are reported in `ChunkQAResult` but are not themselves pass/fail bounds
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
- **Five API endpoints** under `/admin/cleanup-feedback` (registered in `admin/cleanup_rules.py`) — create, list (scoped), categories (aggregation), get by ID, delete
- **Metrics aggregation endpoint** (`GET /admin/looking-glass/metrics`) — workflow status distribution, node failure rates, HITL escalation rates, auto-accepted counts, cleanup-feedback category counts (scoped by tenant/project/corpus)

### Rule Suggester (`atlas.rule_suggester`)

LLM-assisted cleanup rule suggestion (Phase 7D):

- **`suggest_cleanup_rule()`** — accepts sample markdown + issues + optional context, calls the configured LLM, returns `{rule_yaml, rationale}`
- **Heuristic fallback** — `_heuristic_suggestion()` detects hard-wrapped paragraphs, mixed bullets, setext headings, header/footer keywords, OCR artifacts
- **Deterministic provider branch** — `DeterministicProvider._suggest_rule_json()` returns stable suggestion JSON for CI/test
- **Endpoint**: `POST /admin/cleanup-rules/suggest` (resolves `chat_model` → `refine_model` fallback). Sibling rule endpoints: `apply`, `dry-run`, `export`, `import`, and `DELETE /admin/cleanup-rules/{rule_name}`

### Cleanup & Tuning Admin Page

React SPA admin page (Phase 7E) for operator self-service, served at `/app/admin/cleanup`:

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

Unified pipeline runner (1203 lines) with shared helpers:

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

Current automated coverage (**773 tests across 58 files** — 767 in the CI unit shard, 6 `integration`-marked tests in a separate job that skip without cached models; 2026-08-30):
- Schema creation and validation (incl. `CleanupResult`, `JudgeResult.sub_scores`)
- Diagnostics and error handling
- Pipeline state transitions (11 nodes)
- HITL priority calculations
- Admin and RAG endpoints (incl. fidelity mode search filter)
- Config management
- Qdrant integration
- Retry/backoff (`test_retry.py` — 14 tests)
- Chunk QA + fallback (`test_chunk_qa.py` — 9 tests)
- Cleanup node (`test_cleanup.py` — 23 tests)
- Docling health score (`test_docling_health.py` — 15 tests)
- Routing logic (`test_routing.py` — 24 tests + `test_routing_layout.py`)
- **Cleanup rules engine** (`test_cleanup_rules.py` — 50 tests + `test_cleanup_layout.py`)
- **Cleanup feedback API** (`test_cleanup_feedback.py` — 7 tests)
- **Metrics aggregation** (`test_metrics_aggregation.py` — 3 tests)
- **Rule suggestion** (`test_rule_suggestion.py` — 13 tests)
- **Phase refactors** (`test_phase_refactors.py` — 40 tests): refine guardrails, normalize boundary, runner consolidation, html_unescape dedup
- **Cleanup rules import/export** (`test_cleanup_rules_import_export.py` — 10 tests)
- **Layout parser** (`test_layout_types.py`, `test_model_manager.py`, `test_postprocess.py`, `test_layout_ingest_wiring.py` — 64 tests)
- **LLM artifact stripping** (`test_llm_artifact_stripping.py` — 37 tests)
- **LLM profiles** (`test_llm_profiles.py` — 18 tests): profile resolution, both-config patching, immutable `embed_model` guard
- **Token/context budgeting** (`test_tokens.py` — 15 tests) and oversize guards (`test_oversize_guards.py`)
- **Refine node** (`test_refine_node.py` — 20 tests)
- **VLM ingest** (`test_vlm_stitcher.py` — 22 tests, `test_vlm_ingest_session.py` — 25 tests, `test_vlm_ingest_api.py` — 23 tests): stitcher dedup/merge, session lifecycle, API endpoint contracts

Run tests:
```bash
pytest -q                    # All tests (773; CI shards them, see .github/workflows/ci.yml)
pytest -m "not integration"  # Unit shard (what CI's backend job runs)
pytest -m integration --no-cov   # Integration shard (skips without cached Docling models)
```

## Next Steps

Remaining work is tracked in `docs/ACTION_ITEMS.md`. The current implementation
(unreleased work on top of v0.8.0) provides:
- ✅ All core data models (incl. multi-dimensional judge, cleanup results, cleanup feedback)
- ✅ Full 11-node pipeline (Ingest → Cleanup → Judge → Refine → Metadata → Embeddings → Chunking → Commit + HITL/COMPLETED/FAILED)
- ✅ Config-driven cleanup rules engine (8 step types, first-match-wins, rule-tag routing)
- ✅ Cleanup feedback capture + metrics aggregation API
- ✅ LLM-assisted rule suggestion (`POST /admin/cleanup-rules/suggest`)
- ✅ Cleanup & Tuning UI card in Admin tab
- ✅ Retry/backoff on all external calls
- ✅ Chunk QA with automatic fallback
- ✅ Docling integration with health scoring
- ✅ Unified routing with fail-fast, floor checks, cleanup-rejudge, rule-tag escalation
- ✅ Diagnostics with structured error codes and trace levels
- ✅ HITL workflow
- ✅ Enhanced chunking (3 strategies + QA)
- ✅ Fidelity mode search filter

All Phase 7–11 items are complete. Phase 12A–C and 12E are complete; 12D (VLM quality audit) is still open and was incorrectly closed as GitHub #30.
- ✅ Layout-aware PDF parser (8 ONNX modules from RAGFlow deepdoc) with swappable backends
- ✅ Refine content-safety guardrails, sectional refinement, LLM artifact stripping
- ✅ Token utilities, dynamic max_tokens, section-count preservation guard
- ✅ Normalize refactored to formatting-only; page-number and noise stripping moved to cleanup builtins
- ✅ Runner consolidation (5 shared helpers, 37% line reduction)
- 🔄 Prompt/rubric tuning as real-world usage data accumulates
- 🔄 Retrieval upgrades (hybrid/rerank) deferred unless a measured failure mode requires them (`ACTION_ITEMS.md` P2-07)
- ✅ **React SPA Control Center (Phase 12→13)** — Full operator console (`web/`, ~76 source files). Vite 8 + React 18 + TypeScript + shadcn/ui + Tailwind CSS. Routes under `/app`: Dashboard (index), `ingest`, `library`, `search`, `review`, `admin/{health,cleanup,groups,danger}`, and the Document Editor (PDF.js + CodeMirror 6) at `doc/:docId` / `run/:runId`. Builds to `static/app/`, served by FastAPI at `/app`.
- ✅ **Vision Language Model (VLM) integration** — multimodal `ChatMessage`, `page_renderer.py` (PyMuPDF), `vision_model` role in `models.yaml`, unclosed `<think>` tag handling
- ✅ **VLM-first parser backend (Phase 12E)** — `backend: vision` parser mode. `vlm_ingest` package (deterministic stitcher + session manager), 16-endpoint API router (`api_vlm_ingest.py`), unified React ingest wizard (`/app/ingest`), headless mode via `VisionParser` (`backend: vision`). Server-side config export; import is handled client-side.
- ✅ **Hosted generation via LLM profiles** — `openrouter` (OpenAI-compatible) provider selected by the `api` profile, which is the default `active_profile`. `ATLAS_LLM_PROFILE` or `models.yaml → active_profile` patches both `models.yaml` and `pipeline.yaml`. Zero-data-retention enforced per request (`provider: {zdr: true}`). Embeddings pinned to the CPU `embeddings` sidecar (host `18090`) and excluded from profile switching. Atlas API on host `28080`.
- ✅ **Judge context budgeting** — `limits.judge_max_context_tokens`; oversized documents skip grading rather than failing ingest.
