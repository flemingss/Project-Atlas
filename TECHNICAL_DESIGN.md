# Project Atlas — Technical Design (Aug 2026)

This document is the build-continuity technical design for Project Atlas. It covers:

1) **Original intent**
2) **Current implementation** (what is actually in this repo today)
3) **End-state target architecture** (including pivots discussed)
4) A **roadmap to completion** with explicit status markers
5) A **“fantasy check”**: items that are high-risk / likely to balloon / require choices

---

## Status legend

- ✅ **Implemented**: exists in repo and is exercised/tested
- 🟨 **Partial**: exists but not fully wired, or behavior is placeholder
- 🛠 **Planned**: agreed direction, not implemented
- 💭 **Aspirational / risk**: plausible but not committed; may be replaced/simplified

---

## 1) Product goal (end-state)

Atlas is a **local-first “RAG package preparation appliance”**:

- Ingest domain documents
- Produce **professional-grade, traceable chunks** + metadata
- Enforce multi-tenancy (`tenant_id`, `project_id`, `corpus_id`)
- Provide a **HITL checkpoint** when automation cannot meet quality thresholds
- Export a repeatable "RAG package" (manifest + enriched markdown) for downstream systems

The intended operator is a **non-technical domain expert** who can run the appliance via the **React SPA operator UI** (served at `/app`), ingest documents, and resolve HITL tasks without touching code.

---

## 2) Original design intent (baseline requirements)

The original high-level design describes an agentic loop:

1. **Ingest** (Docling) → Docling JSON ground truth + markdown projection
2. **Judge** (few-shot rubric) → score 1–5 + rationale + versioning
3. **Refine** (if score < 4) → max 2 retries → then HITL
4. **Metadata** tiering (tier1 local for most, tier2 frontier/70B for borderline/complex) with caps
5. **Embeddings** as a decoupled module; every chunk records embedding trace
6. **Chunking** heading-aware with section hierarchy
7. **Commit** to vector store with idempotency + supersession chains

Retrieval-time logic:

- Hybrid search (dense + keyword)
- Filters: `is_finalized=true`, `tenant_id` (and optionally `fidelity_flag`)
- Optional rerank (cross-encoder)

Ops:

- Concurrency/resource guard
- Structured diagnostics + trace levels
- HITL hub (originally Dify) with priority queue semantics
- Rollback / version recovery

---

## 3) Current repo implementation (truth as of today)

### 3.1 Core service

- ✅ FastAPI app with health/admin/rag routes (`src/atlas/api.py` + routers)
- ✅ Root endpoint `GET /` for service info (links to health/docs/admin/rag)
- ✅ Settings via `pydantic-settings` (`src/atlas/settings.py`, `.env.example`)
- ✅ Postgres connection + schema creation (SQLAlchemy) for:
  - config versions
  - durable run ledger (workflow runs / node runs / artifact refs)
  - HITL tasks
  - active doc versions
  - cleanup feedback

### 3.2 Config management

- ✅ YAML defaults: `config/pipeline.yaml`, `config/models.yaml`
- ✅ DB-backed config snapshots + activation (table `config_versions`)
- ✅ Admin endpoints to view effective config and create/activate config versions

Notes:

- Config snapshots are whole payloads `{pipeline, models, _meta}` with stable hash.

### 3.3 RAG MVP (current runtime path)

- ✅ `POST /rag/ingest/text`
  - runs through the pipeline runner (`ingest_text_via_pipeline`) with the full agentic loop (ingest → judge → refine → metadata → chunk → commit)
  - three chunking strategies available (configurable via `pipeline.yaml`): `paragraph`, `hierarchical`, and `semantic` (default, token-aware markdown-heading chunker)
  - embed via `ModelRegistry.resolve('embed_model')` + provider `embed()`
  - upsert to Qdrant collection `atlas_chunks`
  - writes a durable run ledger row + node runs + artifact refs
  - deterministic chunk IDs (`tenant_id` + `project_id` + `corpus_id` + `doc_id` + `doc_version` + `content_hash` + `chunk_index`)
  - computes and stores `fidelity_flag` on each chunk (verified / partial / low_confidence / needs_review)
  - normalizes markdown (formatting-only: whitespace and line-break normalization) before chunking

- ✅ `POST /rag/ingest/file`
  - stores the original file as an artifact
  - uses layout-aware ONNX parser or Docling for PDF/Office parsing with guardrails (max bytes, max pages, timeout, quality gates)
  - Five parser backends, built as strategies in `pipeline/parsers.py`: `auto` (Docling first → layout fallback), `auto_layout` (layout first → Docling fallback), `vision` (VLM per-page), `layout` (layout only), `docling` (Docling only). Selection is whole-document, not per-page.
  - persists Docling JSON ground truth + markdown projection as artifacts
  - surfaces `extraction_meta` (backend, OCR confidence, layout confidence, scanned flag) in the response
  - falls back to text extraction when both parsers are unavailable

- ✅ `POST /rag/search`
  - embed query via same embed provider
  - Qdrant vector search with must filters:
    - `tenant_id`
    - `project_id`
    - `corpus_id`
    - `is_finalized == true`
    - `is_active_version == true` (doc_version rollback/activation)
  - `fidelity_mode` parameter: `verified` (default — only verified chunks), `verified+partial` (verified + partial), `all` (no fidelity filter)

### 3.4 LLM provider abstraction

- ✅ Provider protocol `ILlmProvider` with `chat()` and `embed()`; multimodal `ChatMessage` (`content: str | list[dict]`) for vision calls
- ✅ OpenAI-compatible provider (`llm/openai_compat.py`) backs every real backend — LM Studio, OpenRouter, and the embeddings sidecar. Strips closed and unclosed `<think>` blocks, logs `finish_reason`, raises on `length` truncation
- ✅ Deterministic provider for repeatable local tests: `embed()` returns stable SHA-256-derived vectors; `chat()` returns heuristic responses for judge (score/rationale), refine (improved markdown), and metadata (JSON tags)
- ✅ **Hosted generation through OpenRouter**: the `openrouter` provider (an OpenAI-compatible gateway) is implemented and is the shipped default. **Zero-data-retention is enforced per request** — `enforce_zdr: true` adds `provider: {zdr: true}` to every request body, restricting routing to ZDR-compliant endpoints
- ✅ **LLM profiles** (`llm/profiles.py`): a profile is a named patch applied over **both** `models.yaml` and `pipeline.yaml`, so model ids and the tuning that has to move with them switch together. Selected by `ATLAS_LLM_PROFILE`, else `active_profile` in `config/models.yaml`:
  - `local` — generation on LM Studio; `max_context_tokens: 16384`, `refine_max_section_tokens: 6000`, `judge_max_context_tokens: 32000`
  - `api` — generation on OpenRouter (default); `max_context_tokens: 1048576`, `refine_max_section_tokens: 50000`, `judge_max_context_tokens: 1310720`
- ✅ **Embeddings are infrastructure, not a profile choice**: `embed_model` sits in `PROFILE_IMMUTABLE_ROLES` and a profile that tries to override it fails at load. Embeddings are served by a CPU **`embeddings` sidecar** in docker compose (`text-embeddings-inference`, reachable as `http://embeddings:80` on the compose network, host port `18090`). The Atlas API itself is on host port `28080`
- ✅ **Privacy posture**: there is no sensitivity-based provider-routing block. `is_sensitive` remains a per-document flag (it reaches Qdrant payloads, the HITL priority multiplier, and exports) but does **not** gate provider selection; the privacy guarantee rests on enforced zero-data-retention

### 3.5 Vector store integration

- ✅ `QdrantStore` wrapper
- ✅ `ensure_collection()` validates dimension mismatch on existing collection (clear error)

### 3.6 Pipeline modules

- ✅ Pipeline state machine (`PipelineStateManager`) and orchestrator (`PipelineOrchestrator`) under `src/atlas/pipeline/*`
- ✅ 11-node pipeline: INGEST → CLEANUP → JUDGE → REFINE → METADATA → EMBEDDINGS → CHUNKING → COMMIT (+ HITL, COMPLETED, FAILED)
- ✅ **Cleanup node** (`pipeline/cleanup.py`): deterministic markdown cleanup between Ingest and Judge. Five built-in transforms (normalise whitespace, strip broken links, repair heading hierarchy, strip trailing whitespace, static checks) plus five configurable builtin extraction-artifact fixes via the `builtin_cleanup:` config section — `html_unescape`, `fix_ligatures`, `strip_zero_width_chars` and `strip_page_numbers` are ON by default; only `strip_repetitive_lines` is OFF by default (`strip_page_numbers` is additionally skipped for `parse_profile == "pdf_layout"`). Produces `CleanupResult`. Accepts optional `doc_context` and `config` to apply config-driven cleanup rules after built-in transforms.
- ✅ **Cleanup rules engine** (`pipeline/cleanup_rules.py`): declarative, first-match-wins rule engine for per-corpus / per-mime-type cleanup. Eight step handlers (`_STEP_REGISTRY`): `strip_lines_matching`, `rewrite_pattern`, `strip_headers_footers`, `normalize_headings`, `fix_numbered_headings`, `merge_hardwrapped_paragraphs`, `fix_bullets`, `html_unescape`. Rules configured in `pipeline.yaml` `cleanup_rules:` section; the stock config ships `cleanup_rules: []` (commented examples inline) and the reference rule set lives in `personal_configs/`. Rule tags (`hard_failure`, `suspicious_content`, `auto_fix_only`) influence routing decisions. `html_unescape` handler delegates to the shared builtin implementation.
- ✅ **Judge node** (`pipeline/judge.py`): multi-dimensional rubric — FAITHFULNESS, FORMATTING, COHESION, HALLUCINATION_RISK (each 1–5). Composite score = rounded mean. Per-dimension rationale for scores below 4 with specific issues and improvement guidance. Four few-shot examples including a mixed-score example. Error fallback `score=3` / `needs_refinement=False` (neutral — transient failures don’t burn retries). Legacy single-SCORE fallback preserved. Versioned `judge_version` (model + prompt hash). **Oversize guard**: the judge embeds the whole document in its prompt with no truncation, so documents estimated above `limits.judge_max_context_tokens` skip grading instead of failing ingest with an over-length request — they are still cleaned, chunked, embedded and searchable, just not quality-gated or refined.
- ✅ **Refine node** (`pipeline/refine.py`): configurable max retries, triggers HITL when exhausted. Receives rich judge context (sub-scores with focus markers, rationale, iteration context). Content-safety guardrails: tightened system prompt ("MUST NOT summarise, condense, or omit"), `min_preservation_ratio` (default 0.85) rejects outputs shorter than 85% of input. Sectional refinement when `tokens.fits_in_context()` fails either ceiling — the context budget (`limits.max_context_tokens`) **or** the refine model's `max_output_tokens` — splitting on headings at `limits.refine_max_section_tokens`. Section-count preservation guard (rejects if ≥20% headings lost). LLM artifact stripping (`strip_llm_artifacts()`, extracted to `pipeline/guardrails.py`). Dynamic `max_tokens` per call. Only successful refinements count against retry limit; hard cap at 2× max retries for total attempts. Versioned `refine_version` v2.
- ✅ **Metadata node** (`pipeline/metadata.py`): fully implemented and on the runtime path (not a scaffold). Runs **once per document**, not per chunk, and only sees `content[:1000]`. Tier 2 (`metadata_tier2_model`) is selected for borderline judge scores (3–4), subject to `limits.tier2_chunk_cap_per_document`; the single resulting tag set is copied onto every chunk payload.
- ✅ **Unified routing** (`pipeline/routing.py`): `decide_next_step()` pure function centralises all branching (fail-fast on composite ≤ `fail_fast_score`, per-dimension floor checks, cleanup-rejudge with cycle guard (max 1), standard refine/HITL paths, rule-tag-aware cleanup routing — `hard_failure`→FAILED, `suspicious_content`→HITL). `RoutingDecision` includes `rollback: bool` field. Score regression rollback (refine made score worse → revert markdown, route appropriately). Diminishing-returns detection (score unchanged after refine → HITL). `content_ok` check includes `hallucination_risk`. `PipelineStateManager.get_next_node()` delegates here.
- ✅ **Retry/backoff** (`retry.py`): `RetryConfig` + `async_retry()` / `sync_retry()` decorators. Applied to `openai_compat.py`, `qdrant_store.py`, `docling_adapter.py`. Config-driven per subsystem via `pipeline.yaml` `retry:` section.
- ✅ **Chunk QA + fallback** (`rag/chunk_qa.py`): `validate_chunks()` gates on `min_chunk_count`, `max_token_ratio_limit`, `max_duplication_ratio` and `min_coverage_ratio`; min/max/average token counts are reported in `ChunkQAResult` but are not pass/fail bounds. On failure `chunk_with_fallback()` retries a simpler strategy (semantic→paragraph, hierarchical→paragraph). Bounds configurable via `pipeline.yaml` `chunking.qa:` section.
- ✅ **Docling health score** (`ingest/docling_health.py`): `compute_health()` evaluates extraction quality signals into a 1–5 composite score, stored on `PipelineContext`.
- ✅ Pipeline runner is wired into `/rag/ingest/*` endpoints (text + file) and executes the full agentic loop with cleanup, chunk QA fallback, and health scoring. Runner is 1203 lines with 5 shared helpers. HITL tasks include rich context (judge sub-scores, rationale, score history, refine attempts). HITL resume guarded by `MAX_HITL_RESUMES=2`. `max_refine_retries` read from `limits` section with backwards-compat fallback.
- ✅ No LangGraph dependency exists today

Notes:

- Prompts/parsing are v1 and will evolve; the pipeline contract is stable.
- Pipeline config (`pipeline.yaml`) includes `retry:`, `chunking.qa:`, `builtin_cleanup:`, `judge_dim_floors:` (formatting/cohesion floors default to 2), `fail_fast_score`, `cleanup_rejudge` (default `true`), `limits.refine_max_retries` (default 3), `limits.max_context_tokens`, `limits.refine_max_section_tokens`, `limits.judge_max_context_tokens`, and `cleanup_rules:` (ships empty). The three `limits.*` context values are profile-driven.
- Removed from `pipeline.yaml`: the `frontier_fallback:`, `cache:` (a semantic cache was never implemented) and `privacy:` blocks, along with `thresholds.judge_borderline_low/high`. Settings `atlas_redis_url`, `atlas_layout_table_extraction` and `atlas_heavy_task_limit` are likewise gone.

### 3.7 HITL

- ✅ Durable HITL tasks are persisted in Postgres (`hitl_tasks`) with a small CRUD/queue API under `/admin/hitl/*`
- ✅ HITL priority scoring and CRUD live in `hitl_ledger.py`; routes live in `admin/hitl.py`. The old in-memory `src/atlas/hitl.py` manager (`HITLManager` / `get_hitl_manager`) has been deleted — the durable ledger is the only path
- 🟨 “Push to Dify” remains explicitly a placeholder/optional experiment
### 3.8 Cleanup feedback & metrics

`api_admin.py` is now a thin (175-line) aggregator; the admin surface lives in `src/atlas/admin/*` (`cleanup_rules.py`, `config.py`, `exports.py`, `hitl.py`, `looking_glass.py`, `maintenance.py`, `scope.py`, `workflow.py`).

- ✅ **Cleanup feedback model** (`models.py`): `CleanupFeedback` table with tenant/project/corpus/doc/chunk scoping, category, description, source spans, run_id FK, and metadata JSON.
- ✅ **Feedback ledger** (`feedback_ledger.py`): CRUD helpers — `create_feedback`, `get_feedback`, `list_feedback`, `delete_feedback`, `feedback_category_counts`.
- ✅ **Cleanup feedback API** (`admin/cleanup_rules.py`): five endpoints under `/admin/cleanup-feedback` — create (POST, 201), list (GET, scoped), categories (GET, aggregation), get by ID, delete by ID.
- ✅ **Metrics aggregation API** (`admin/looking_glass.py`): `GET /admin/looking-glass/metrics` with optional tenant/project/corpus scoping. Returns workflow status distribution, node failure rates by node name, HITL escalation rates, auto-accepted counts, and cleanup-feedback category counts.
- ✅ **LLM-assisted rule suggestion** (`rule_suggester.py`): `suggest_cleanup_rule()` accepts sample markdown + issues, calls the LLM (via `ModelRegistry`), and returns `{rule_yaml, rationale}`. Heuristic fallback when LLM unavailable. Deterministic provider branch for CI.
- ✅ **Rule suggestion endpoint** (`admin/cleanup_rules.py`): `POST /admin/cleanup-rules/suggest` — resolves `chat_model` or `refine_model`, invokes `suggest_cleanup_rule()`, returns JSON suggestion.
- ✅ **Cleanup rules import/export** (`admin/cleanup_rules.py`): `GET /admin/cleanup-rules/export` downloads active rules as YAML. `POST /admin/cleanup-rules/import` accepts YAML with `replace` (overwrite all) or `merge` (add/update by name) modes. Both validate rules against the schema before applying.

### 3.9 E2E and repeatability

- ✅ Unit tests and integration tests exist and pass in this repo
- ✅ Black-box E2E runner exists (`src/atlas/e2e/scenarios.py`):
  - starts Docker infra (minimal compose)
  - starts the API
  - runs a suite of API scenarios (admin + rag + pipeline + HITL)
  - uses deterministic providers (chat + embeddings) automatically to avoid LM Studio dependency
  - scenarios include: config activation, tenant isolation, finalized filter, idempotent upserts, refine-then-pass, HITL escalation and resume, batch multi-doc ingest, workflow orchestration validation, error recovery, and Looking Glass endpoints
- ✅ `POST /admin/self-test` runs the E2E suite against the running appliance in-place
- ✅ Optional `--mode local_llm` for E2E testing against a real OpenAI-compatible LLM server

### 3.10 Operator UI

- ✅ **React SPA Control Center** (`web/`, served at `/app`)
  - Routes (SPA `basename="/app"`, mounted only at `/app`): index → Dashboard, `ingest`, `library`, `search`, `review`, `admin/{health,cleanup,groups,danger}`, and the Document Editor at `doc/:docId` / `run/:runId`. `upload` and `vlm-ingest` are redirects to `ingest`; there is no `/app/editor` route
  - Admin sub-pages: Health/Diagnostics, Cleanup & Tuning, Groups, Danger Zone (DB reset)
  - Stack: Vite 6 + React 18 + TypeScript + shadcn/ui + Tailwind CSS + Zustand + TanStack React Query
  - Builds to `static/app/` via `npm run build`; Dockerfile multi-stage build
  - PDF.js viewer + CodeMirror 6 markdown editor with resizable panels
  - Tool palette: VLM Fix, LLM Refine, Strip Artifacts, Re-Judge, Save, Undo
  - See `web/README.md` for developer guide
- ✅ **Unified Ingest wizard** (`web/src/pages/ingest/ingest-page.tsx`, served at `/app/ingest`)
  - One 6-step workflow covering the text, Docling and VLM methods: method + upload → configure → pages (VLM) → process → review/stitch → commit
  - PDF preview with red crop guide overlays and a mask editor, Fit Page/Width/Actual zoom modes (ResizeObserver auto-fit)
  - Auto-advance page processing, per-page markdown correction, config export (import is handled client-side)
  - Session-expired recovery: red banner on backend 404, conditional polling stop
  - Backend: 16-endpoint API router (`api_vlm_ingest.py`), auto-creates workflow run on commit for uploaded PDFs
  - The earlier separate `upload/` and `vlm-ingest/` pages have been deleted
  - Headless mode: `VisionParser` (`backend: vision`) in `pipeline/parsers.py`, configured via `pipeline.yaml → pdf_parser.vlm`

### 3.11 Export and corpus management

- ✅ Doc-level export (`GET /admin/docs/{doc_id}/export`): zip containing manifest.json, document.md (enriched markdown with YAML frontmatter), index.json, index_config.json, chunk_manifest.jsonl, and artifact files
- ✅ Lean export format: markdown-only zip for handoff to external RAG pipelines
- ✅ Corpus-level export (`GET /admin/corpora/{corpus_id}/export`): zip of per-document export packages
- ✅ Corpus import (`POST /admin/corpora/{corpus_id}/import`): ingest all docs from an exported corpus zip
- ✅ Doc deletion (`DELETE /admin/docs/{doc_id}`): removes Qdrant points + active_doc_version row
- ✅ DB reset (`POST /admin/db/reset`): clear Postgres tables, Qdrant points, and/or artifact files (strict auth required)

### 3.12 Doc versioning and rollback

- ✅ `active_doc_versions` table tracks the active version per (tenant, project, doc)
- ✅ `GET /admin/docs/{doc_id}/active-version` and `POST /admin/docs/{doc_id}/active-version` for version management
- ✅ Rollback sets Qdrant `is_active_version` payload flags; search filters on `is_active_version == true`

### 3.13 PDF ingest hardening

- ✅ Configurable guardrails via settings: `ATLAS_PDF_MAX_BYTES`, `ATLAS_PDF_MAX_PAGES`, `ATLAS_DOCLING_TIMEOUT_S`
- ✅ Quality gates on PDF markdown output: min chars, min words, alpha ratio, garbled ratio thresholds
- ✅ PDF preflight (best-effort via PyMuPDF): page count, encryption detection, metadata extraction
- ✅ Docling timeout and error handling with structured error codes

---

## 4) Pivots and decisions incorporated

These are the directional changes discussed, reflected as **Planned** unless implemented.

### 4.1 Dify

- ✅ Pivot: **Dify is not the default RC UI/HITL hub**.
- ✅ Dify is behind a Docker Compose profile (`--profile dify`); the default compose boots without it.
- ✅ Atlas owns workflow state and HITL tasks; the React SPA operator UI provides monitoring and HITL interaction.

Rationale:

- Appliance-grade repeatability favors fewer moving parts
- A general-purpose workflow UI tends to create “mysterious state” and provisioning complexity

### 4.2 LangGraph

- 🛠 Decision: LangGraph can be introduced **only if it reduces risk** (checkpointing/pausing/trace), but it is not required for the next RC step.
- 🛠 If introduced, treat it as an internal implementation detail behind stable Atlas contracts.

### 4.3 “Black box” operation

- ✅ Target: keep the system black-box from the UI perspective.
- ✅ Minimal monitoring + HITL pane for operators is delivered via the React SPA UI.

---

## 5) Target end-state architecture (what “complete” looks like)

### 5.1 End-state modules

1) **Ingest subsystem**
   - ✅ Docling integration for PDF/Office; store ground truth JSON
   - ✅ Markdown projection with traceability fields
   - ✅ PDF-specific guardrails (size, page count, timeout, quality gates)

2) **Agentic pipeline (automation loop)**
   - ✅ Deterministic cleanup node (5 built-in transforms + 5 configurable builtins) between Ingest and Judge
   - ✅ Config-driven cleanup rules engine (8 step handlers, first-match-wins, rule-tag routing)
   - ✅ Multi-dimensional judge (4 dimensions: faithfulness, formatting, cohesion, hallucination_risk)
   - ✅ Unified routing with fail-fast, per-dimension floor checks, cleanup-rejudge (cycle-guarded), rule-tag-aware escalation, score regression rollback, diminishing-returns detection
   - ✅ `RoutingDecision` with `rollback: bool` field for structured rollback signalling
   - ✅ Refine content-safety guardrails (min_preservation_ratio, tightened prompt, refine_version v2)
   - ✅ Refine with real provider calls + retry limits
   - ✅ Metadata tiering with enforced caps
   - ✅ Embeddings + chunking + commit integrated as first-class pipeline steps
   - ✅ Markdown normalization (formatting-only: whitespace and line-break normalization) before chunking
   - ✅ Chunk QA with automatic fallback chain
   - ✅ Retry/backoff on all external calls (LLM, vectorstore, Docling)
   - ✅ Docling health scoring after every ingest

3) **Durable workflow state**
   - ✅ Document/job table(s): ingest runs, current node, timestamps, error codes
   - ✅ Node-run trace table(s): inputs/outputs references, model/provider versions, durations
   - ✅ Artifact store (filesystem first; DB stores references)

4) **HITL**
   - ✅ Durable HITL task model (DB)
   - ✅ Task lifecycle: pending → in_progress → completed/skipped/rejected
   - ✅ Re-run behavior: resolve edit → resume pipeline from a defined checkpoint (`POST /admin/hitl/tasks/{id}/resume`)

5) **Retrieval-time enhancements**
   - 💭 Hybrid search + rerank (see Fantasy Check for risk/alternatives)

6) **Professional output**
   - ✅ Export “RAG package”:
     - manifest.json
     - enriched markdown chunks with YAML frontmatter
     - index describing doc/chunk relationships
   - ✅ Lean export format (markdown-only zip)
   - ✅ Corpus-level export + import

7) **Appliance-grade operations**
   - ✅ One-command bring-up (`docker compose up`)
   - ✅ Built-in self-test (`POST /admin/self-test`)
   - ✅ Auth + safe defaults (no default secrets in non-dev)
   - ✅ DB reset endpoint for clean re-import
   - 🛠 Backups/upgrade guidance

8) **Repo Looking Glass (corpus inspection)**
  - ✅ Goal: operators can assess corpus health/state **without exporting packages**
  - ✅ Scope: read-only inspection of documents/chunks/runs/HITL + storage/Qdrant health
  - ✅ Non-goals: not a general-purpose UI platform; not a full labeling system; no “mysterious state”
  - ✅ Minimum query surface (API-first; React SPA surfaces these):
    - corpus inventory: document counts, chunk counts, finalized/non-finalized, by tenant/project
    - document view: doc metadata, available doc_versions, version activation/finalization state
    - chunk preview: show stored chunk text + key metadata (doc_id, doc_version, source spans)
    - run ledger: list ingest runs + current status + last error code + timestamps + in-flight/failure views
    - HITL queue: list tasks and link them to runs/docs
    - storage health: Qdrant collections + vector dim + point counts; artifact store presence    - **metrics aggregation**: workflow status distribution, node failure rates, HITL escalation rates, cleanup-feedback category counts (scoped by tenant/project/corpus)

10) **Cleanup feedback loop**
   - ✅ Durable feedback model (`cleanup_feedback` table) with tenant/project/corpus/doc/chunk scoping
   - ✅ CRUD API under `/admin/cleanup-feedback` for operators to report cleanup quality issues
   - ✅ Category-based aggregation for identifying systematic cleanup problems
   - ✅ LLM-assisted rule suggestion (on-demand endpoint to propose new cleanup rules from feedback patterns)
   - ✅ Admin UI "Cleanup & Tuning" page in React SPA
9) **Operator UI**
   - ✅ **React SPA Control Center** (`web/`, served at `/app`) — full operator console (Dashboard, Ingest, Library, Search, Review, Editor, Admin) with Vite 6 + React 18 + TypeScript + shadcn/ui + Tailwind CSS. Builds to `static/app/`.
   - ✅ **Document Editor** — PDF.js + CodeMirror 6 + VLM tools at `/app/doc/{docId}` and `/app/run/{runId}`. See `web/README.md`.
   - ✅ **Unified Ingest wizard** — 6-step ingest at `/app/ingest`, covering text, Docling and VLM methods. PDF preview with crop overlays and mask editor, session-expired recovery, auto-create workflow run on commit for uploads. Headless VLM mode via `VisionParser` (`backend: vision`).

---

## 6) Roadmap to completion (phased, testable)

This is sequenced to maximize repeatability and minimize fantasy risk.

### Phase 0 — Current baseline (done)

- ✅ Repeatable RAG MVP (ingest/search) with deterministic E2E
- ✅ Config snapshots and activation

### Phase 1 — Appliance-grade core (RC stabilization)

- ✅ Containerize Atlas and add it to compose (Atlas + Postgres + Qdrant)
- ✅ Add a minimal auth mechanism (shared secret header is acceptable for RC)
- ✅ Add “self-test” endpoint/command that runs the E2E scenarios against the running appliance
- ✅ Add a minimal “Repo Looking Glass” read-only API (JSON) sufficient for shakedown:
  - inventory counts and distributions (tenant/project/finalized)
  - Qdrant collection stats (name/dim/points)
  - list docs + show doc detail + show chunk previews
- ✅ Tighten startup validation:
  - validate env vars
  - validate config shapes
  - fail-fast with clear error messages

**Definition of done:** a fresh machine can run one command and get PASS/FAIL deterministically.

### Phase 2 — Durable workflow + HITL (mission capability)

- ✅ Add DB tables for:
  - ingest runs/jobs + node runs + artifact refs
- ✅ Add DB tables for:
  - HITL tasks
- ✅ Artifact refs tracked in DB; large artifacts stored in filesystem artifact store
- ✅ Add API endpoints:
  - create/list/get/resolve HITL tasks
  - list job/runs + status
- ✅ Looking Glass queries now driven off the durable ledger (jobs/node_runs/HITL tables) + Qdrant stats

**Definition of done:** you can force a HITL task in tests, resolve it, and see the resulting committed chunks.

### Phase 3 — Wire the pipeline into the ingest path

- ✅ Replace the `/rag/ingest/*` “direct path” with a pipeline-backed run (text + file)
- ✅ Judge/refine/metadata nodes implemented with few-shot rubrics and configurable retries
- ✅ E2E scenarios cover:
  - `scenario_pipeline_refine_then_pass` (low score triggers refine then passes)
  - `scenario_pipeline_hitl_escalation_and_resume` (max retries triggers HITL task)

**Definition of done:** the pipeline is the default path for ingest and is fully test-covered.

### Phase 4 — Docling ingestion for PDFs/Office

- ✅ Docling parsing implemented with PDF guardrails, preflight validation, timeout, and quality gates
- 🟨 Deterministic regression tests on sample documents (E2E scenarios cover Docling path; dedicated regression corpus not yet built)

**Definition of done:** PDF ingestion is reliable enough for shakedown; failures produce actionable diagnostics.

### Phase 5 — Professional output + rollback

- ✅ Export pipeline artifacts as a zip “RAG package” (manifest + enriched markdown + index)
- ✅ Implement rollback at **doc_version granularity** (via an active-version record + Qdrant payload flag)

**Definition of done:** operators can export a consistent package and recover a prior version.

### Phase 7 — Config-driven cleanup rules & feedback (v0.5.0)

- ✅ **Phase 7A — Cleanup rules engine**: declarative rule engine (`cleanup_rules.py`) with 7 step handlers, first-match-wins resolution, rule tags influencing routing. `CleanupResult` extended with `rules_applied`, `rules_failed`, `fix_counts`, `rule_tags`. CLEANUP→HITL transition added. 34 new tests.
- ✅ **Phase 7B — Feedback capture**: `CleanupFeedback` model + `feedback_ledger.py` CRUD + 5 admin endpoints. 7 new tests.
- ✅ **Phase 7C — Metrics aggregation**: `GET /admin/looking-glass/metrics` with workflow/node/HITL/feedback aggregation, scoped by tenant/project/corpus. 3 new tests.
- ✅ **Phase 7D — LLM-assisted rule suggestion**: on-demand endpoint (`POST /admin/cleanup-rules/suggest`) that accepts sample markdown + observed issues, calls LLM to suggest cleanup rule YAML. Heuristic fallback when LLM unavailable. Deterministic provider branch for CI. 13 new tests in `test_rule_suggestion.py`.
- ✅ **Phase 7E — Admin UI "Cleanup & Tuning" page**: Cleanup & Tuning admin page for viewing active rules, submitting feedback, browsing categories, viewing pipeline metrics, and invoking AI-assisted rule suggestion with inline YAML preview.

**Definition of done (7A-7E):** All items implemented and tested.

### Phase 8 — Content-safety & codebase refactoring (v0.6.0)

- ✅ **Phase 8A — Refine content-safety guardrails**: Tightened `REFINE_SYSTEM_PROMPT`, added `min_preservation_ratio` (default 0.6), post-refine length check rejects outputs < 60% of input. Bumped `refine_version` to v2. Fixed double system-prompt bug.
- ✅ **Phase 8B — Normalize/cleanup boundary**: Normalize refactored to formatting-only (whitespace/line-break normalization). `strip_noise_markdown` removed. Page-number stripping and repetitive-line removal moved to cleanup builtins (`strip_page_numbers` ON by default, `strip_repetitive_lines` OFF by default).
- ✅ **Phase 8C — Runner consolidation**: Five shared helpers extracted. Both ingest paths rewritten to use shared helpers. 37% line reduction (1572 → 996 lines). All silent `except: pass` replaced with `log.warning`. Hoisted inline imports to module top. Normalize tracked as a pipeline node run.
- ✅ **Phase 8D — Polish**: `html_unescape` dedup (cleanup_rules delegates to cleanup builtin). 40 new tests in `test_phase_refactors.py`.
- ✅ **Cleanup rules import/export**: `GET /admin/cleanup-rules/export` (YAML download) and `POST /admin/cleanup-rules/import` (replace/merge modes). UI export/import controls in operator console. 10 new tests in `test_cleanup_rules_import_export.py`.

**Definition of done (8A-8D + import/export):** 485+ tests passing. Runner consolidated. Refine guardrails prevent content loss. Normalize is formatting-only. Cleanup rules portable via YAML.

### Phase 9 — Pipeline quality improvements (v0.7.0)

- ✅ **Phase 9A — Rich judge-to-refine context**: Refine node receives judge sub-scores (with "← focus here" markers for low dimensions), rationale, and iteration context ("Attempt X of Y"). Orchestrator passes full judge result to refine.
- ✅ **Phase 9B — Per-dimension judge rationale**: Judge prompt expanded—rationale covers each dimension scoring below 4 with specific issues and improvement guidance. Fourth few-shot example added (mixed scores).
- ✅ **Phase 9C — Judge error fallback**: Changed from score=1/needs_refinement=True to score=3/needs_refinement=False—transient LLM failures no longer burn refine retries.
- ✅ **Phase 9D — Score regression rollback**: `RoutingDecision` gains `rollback: bool` field. If refine makes the score worse, markdown is reverted to pre-refine version. Routes to metadata (if pre-refine was acceptable) or HITL.
- ✅ **Phase 9E — Diminishing-returns detection**: If score is unchanged after refine, loop stops and escalates to HITL.
- ✅ **Phase 9F — Cleanup-rejudge cycle guard**: `cleanup_rejudge_count` tracked and capped at 1 to prevent infinite cleanup→judge→cleanup loops.
- ✅ **Phase 9G — Failed refines don't burn retries**: `set_refine_result()` only increments `refine_retries` on success; hard cap circuit breaker at 2× max retries for total attempts.
- ✅ **Phase 9H — Rich HITL context**: HITL tasks store judge sub-scores, rationale, score history, refine attempts, last improvements. UI surfaces this in a collapsible "Judge & refine context" panel.
- ✅ **Phase 9I — HITL resume loop guard**: `MAX_HITL_RESUMES=2` prevents infinite HITL→pipeline→HITL loops.
- ✅ **Phase 9J — Config defaults**: `cleanup_rejudge: true`, `formatting` floor: 2, `cohesion` floor: 2, `refine_max_retries: 3`.
- ✅ **Phase 9K — UI fixes**: Scope-change cache invalidation, HITL resume failure feedback, rich HITL context display, Project dropdown in sidebar, scope-filtered API calls, text-mode upload checkboxes, Groups hierarchy guidance.

**Definition of done (9A-9K):** 485+ tests passing. Pipeline routing significantly improved—fewer unnecessary HITL escalations. Rich context available to operators reviewing HITL tasks.

### Phase 10 — Swappable parser backends & Docling-first default (v0.7.2)

- ✅ **Swappable `pdf_parser.backend`**: IngestNode reads `pipeline.yaml → pdf_parser.backend` (was hardcoded env var). Modes: `auto` (Docling first → layout fallback), `auto_layout` (layout first → Docling fallback), `layout` (deepdoc only), `docling` (Docling only) — later joined by `vision` (Phase 12E). Strategy construction now lives in `pipeline/parsers.py`.
- ✅ **Whole-document selection**: Parser backend chosen once per document (not per-page). This simplifies artifact tracking and avoids stitching inconsistencies.
- ✅ **Config wiring**: `runner.py` passes `pipeline_cfg.get("pdf_parser")` to IngestNode constructor. `pipeline.yaml.example` and `PIPELINE_REFERENCE.md` updated.

**Definition of done (Phase 10):** Docling is the primary parser for PDF ingest. Operators can override via `pipeline.yaml`. No code changes needed to swap backends.

### Phase 11 — Pipeline quality & cleanup improvements (v0.7.3-dev)

- ✅ **Cleanup rules optimization**: `fix_numbered_headings` added to the step registry (now 8 handlers). The ~10-rule reference set, organized into logical sections (heading normalization, content stripping, bullet/list cleanup, paragraph repair), lives in `personal_configs/` — the stock `config/pipeline.yaml` ships `cleanup_rules: []`.
- ✅ **Builtin cleanup expansion**: `strip_repetitive_lines` added as a configurable builtin (default OFF). `strip_page_numbers` remains ON by default.
- ✅ **Sectional refinement**: Refine node uses section-count guard and `max_tokens` scaled to input length. `min_preservation_ratio` raised from 0.6 → 0.85.
- ✅ **LLM artifact stripping**: `strip_llm_artifacts()` removes leaked `<think>` blocks, markdown fences, preamble/postamble boilerplate. Applied as a post-refine step.
- ✅ **`<think>` tag regex fix**: `openai_compat.py` now strips both closed and unclosed reasoning tags (per-tag compiled pair), logs `finish_reason`, and raises on `finish_reason == "length"` rather than returning truncated text.

**Definition of done (Phase 11):** Docling + improved cleanup produces ≤10% noise in refined output for representative corpus. Artifact stripping catches common LLM leaks.

### Phase 12 — Document Editor & VLM integration (planned)

This phase adds operator tooling for surgical document refinement and a vision-language-model (VLM) pipeline.

**Phase 12A — Backend plumbing ✅ (completed):**
- ✅ Multimodal `ChatMessage`: `content` field becomes `str | list[dict]` to support `image_url` blocks.
- ✅ `page_renderer.py`: PyMuPDF-based page-to-PNG with configurable DPI and header/footer crop margins.
- ✅ `vision_model` role in `models.yaml` (e.g. `qwen2.5-vl-32b` or `qwen3.5-35b-a3b`).
- ✅ `/api/editor/vision-refine` endpoint: accepts `(doc_id, page, section_index)`, renders page, sends to VLM with current markdown, returns corrected markdown.
- ✅ Editor API (`api_editor.py`, prefix `/api/editor`): 10 endpoints — `resolve-doc`, `page-info`, `render-page`, `source-pdf`, `markdown`, `page-markdown`, `vision-refine`, `save-markdown`, `llm-refine`, `re-judge`.

**Phase 12B — Standalone Document Editor ✅ (completed, then superseded by 12C):**
- ✅ Zero-build-step HTML/JS page served by FastAPI at `/editor`. (Now replaced by React SPA — see 12C.)

**Phase 12C — React SPA Document Editor ✅ (completed):**
- ✅ 30-file Vite 6 + React 18 + TypeScript scaffold in `web/`.
- ✅ shadcn/ui (Radix + CVA + Tailwind CSS) component library — 10 UI primitives.
- ✅ Left panel: PDF.js viewer (page nav, zoom, DPI/crop controls for VLM).
- ✅ Right panel: CodeMirror 6 editor (markdown syntax highlighting).
- ✅ Tool palette: VLM Fix, LLM Refine, Strip Artifacts, Re-Judge, Save, Undo.
- ✅ State management: Zustand store + TanStack React Query mutations + Sonner toasts.
- ✅ `npm run build` → `static/app/` (served by FastAPI at `/app`).
- ✅ Dockerfile multi-stage: Node.js build stage + Python runtime stage.
- ✅ Developer guide: `web/README.md`.

**Phase 12D — VLM quality audit (~1 day):**
- 🔲 Automated post-ingest page-level comparison: VLM renders each page → diff against parsed markdown → flag pages with high divergence for HITL review.

**Phase 12E — VLM-first parser backend ✅ (completed):**
- ✅ `backend: vision` mode in `pdf_parser.backend`: render all pages → VLM → stitch markdown.
- ✅ `vlm_ingest` package: deterministic page stitcher (dedup, table merge, heading merge) + session manager (in-memory registry, TTL, config serialization).
- ✅ 16-endpoint API router (`/api/editor/vlm-ingest/*`): start session (run ID or upload), list/get/delete sessions, configure, export config, page analysis, thumbnails, preview (with crop overlay params), process page, process all, stitch, commit, get/update per-page result. There is no import route — config import is client-side.
- ✅ Interactive wizard folded into the unified React ingest page (`/app/ingest`): 6-step workflow (method + upload → configure → pages → process → review/stitch → commit). PDF preview with crop guide overlays and mask editor, Fit Page/Width/Actual zoom modes, auto-advance processing, per-page markdown correction.
- ✅ Headless VLM parse via `VisionParser` in `pipeline/parsers.py` — per-page isolation, deterministic stitch, config from `pipeline.yaml`.
- ✅ Config export for headless reuse across documents (import is applied client-side).
- ✅ 55+ new tests (stitcher, session, API-level) — all passing.
- ✅ Session-expired recovery: red banner UI on backend session loss (404), conditional polling stop, `isSessionNotFoundError()` helper across all mutation hooks.
- ✅ E2E wiring audit: fixed page-correction flicker, commit `runId` update for uploads, polling stop on 404.
- ✅ Auto-create workflow run on commit for uploaded PDFs (no pre-existing `run_id`).
- 🔲 Batch/parallel page processing for throughput.
- 🔲 Cost/latency analysis vs Docling for representative corpus.

### Phase 13 — Retrieval-time upgrades (optional / scoped)

- 🛠 Decision: stay **vector-only** for v1; hybrid/BM25/rerank are out of scope unless a measured failure mode requires them

**Gate (evidence required before hybrid/rerank work):**

- Define a small “golden set” of queries that represent suspected weak spots for dense-only retrieval:
  - exact term / identifier queries (invoice IDs, part numbers)
  - numeric/string matches
  - short queries with rare proper nouns
- Run the harness in scripts/retrieval_eval.py against a representative corpus and record metrics:
  - HitRate@K (did any expected doc appear in the top K?)
  - MRR@K
- Threshold to open hybrid/rerank work (v1 rule of thumb):
  - If HitRate@10 < 0.90 on the golden set for a stable corpus, open a retrieval-upgrade issue.
  - Otherwise, treat failures as ingest/chunking/metadata tuning first.

**How to run:**

- Example golden set: eval/retrieval_golden.example.json
- Command: python scripts/retrieval_eval.py --api-url http://127.0.0.1:28080 --golden eval/retrieval_golden.example.json --out retrieval_report.json

### Phase 14 — LLM profile migration & hosted generation (landed)

Generation moved off a single local GPU and onto a hosted OpenAI-compatible gateway, with the
whole posture (model ids *and* the tuning that has to move with them) switching as one unit.

- ✅ **`openrouter` provider**: OpenAI-compatible gateway wired through the existing `openai_compat` provider — no new provider type.
- ✅ **LLM profiles** (`llm/profiles.py`): `local` (LM Studio) and `api` (OpenRouter). Selected by `ATLAS_LLM_PROFILE`, else `active_profile` in `config/models.yaml`. A profile patches **both** `models.yaml` and `pipeline.yaml`; `api` is the shipped default.
- ✅ **Zero-data-retention enforced per request**: `enforce_zdr: true` adds `provider: {zdr: true}` to every request body. A 400/404/422 under ZDR raises with an explanatory message rather than silently falling back.
- ✅ **Embeddings sidecar**: a CPU `text-embeddings-inference` container in docker compose (`http://embeddings:80` internally, host port `18090`). `embed_model` is profile-immutable — swapping the embedder under an existing corpus corrupts retrieval silently when the dimension matches. Atlas API is on host port `28080`.
- ✅ **Dual-ceiling context check**: `tokens.fits_in_context()` checks the context budget *and* the refine model's `max_output_tokens`. Setting `max_context_tokens` to a model's advertised window without this check silently truncates every long refine.
- ✅ **`limits.judge_max_context_tokens`**: documents above the judge's budget **skip** quality grading instead of failing ingest with an over-length request. They are still cleaned, chunked, embedded and searchable — just not graded or refined.
- ✅ **Removals**: `concurrency.py` (`ConcurrencyGuard` / `ResourceGuard` / `ResourceMetrics` / the only `PrivacyGuard` implementation) and `hitl.py` (`HITLManager`) are deleted, together with the `frontier_fallback:`, `cache:` and `privacy:` config blocks, `thresholds.judge_borderline_low/high`, and the `atlas_redis_url` / `atlas_layout_table_extraction` / `atlas_heavy_task_limit` settings.
- ✅ **Privacy decision**: the sensitivity-based routing block is gone by decision. `is_sensitive` still flags documents (Qdrant payloads, HITL priority, exports) but does not gate provider routing; privacy rests on enforced zero-data-retention.

**Definition of done (Phase 14):** one switch (`ATLAS_LLM_PROFILE`) moves the whole generation posture; embeddings never move with it; no request leaves the appliance without ZDR asserted.

---

## 7) Adjudicated “hard items” (decisions landed)

This section resolves the previously flagged “fantasy” items into explicit scope decisions.

### 7.1 Retrieval: “Hybrid search (BM25) + dense + rerank”

**Decision (v1/end-state staging):**

- ✅ v1 uses **vector-only retrieval + metadata filters** (tenant/project/is_finalized/is_active_version, fidelity_mode filter).
- 🛠 Hybrid keyword search and rerank are **explicitly deferred** until we have a measured failure mode that cannot be fixed by better chunking/metadata.

**Rationale:** hybrid/rerank turns Atlas into a search stack with tuning + eval + ops overhead. It is a great upgrade *when needed*, not a baseline requirement for an appliance RC.

### 7.2 Ingest quality: “Docling + OCR + tables/equations are consistently professional-grade”

**Decision (scope control):**

- ✅ Atlas defines **supported ingest profiles** and expands them intentionally.
- ✅ v1 PDF support targets: “extract best-available text + structure”, with diagnostics + HITL for hard pages.
- ✅ “Perfect tables/equations across arbitrary PDFs” is **not a promise**; it is addressed by:
  - constrained doc profiles + Docling preflight
  - regression corpus tests (E2E coverage; dedicated corpus TBD)
  - clear failure modes + operator workflow (HITL escalation on max-retry exhaustion)

### 7.3 Observability: “Checkpoints automatically become a great trace UI”

**Decision (queryable ledger first):**

- ✅ Persist a small **run ledger** (jobs + node_runs) in Postgres as the source for UI queries.
- ✅ Store large artifacts (docling JSON, prompt bodies, intermediate markdown) in the filesystem artifact store; DB stores references.
- ✅ LangGraph checkpointing is optional and does not replace the ledger.

### 7.4 Versioning/rollback: “Supersedes chains + grace periods + rollback”

**Decision (simplify version semantics for v1):**

- ✅ v1 rollback is implemented at **doc_version granularity**:
  - each ingest run produces a doc_versioned set of chunks
  - “rollback” selects a prior version (and marks it active/finalized) without maintaining deep supersession graphs
- 🛠 Supersedes chains and grace-period hiding are deferred until:
  - operator UX is stable
  - retention requirements are explicit
  - we have a concrete need for per-chunk lineage beyond doc_version

---

## 8) Acceptance criteria (what “complete enough for shakedown” means)

The minimum “human shakedown ready” bar:

- Operators can start the appliance and run a built-in self-test
- Operators can inspect corpus state via a “Looking Glass” (inventory, doc list, chunk preview, Qdrant stats)
- Operators can ingest representative documents (text and PDFs via Docling)
- The system deterministically produces committed chunks and a package export
- When automation fails, operators have a HITL queue to resolve issues
- Every decision is traceable (which model/provider/config, which run, why it paused)
- Operators have a React SPA UI for ingest, corpus browsing, HITL review, and Looking Glass views

---

## 9) Documentation

Project documentation was consolidated in v0.7.3-dev to reduce maintenance burden:

- **Removed**: `HLD.md` (superseded by this doc + ARCHITECTURE.md), `PDF_OVERHAUL_PLAN.md` (completed — absorbed into Phases 10-11), `VALIDATION_REPORT.md` (frozen v0.7.0 snapshot), `CAPABILITIES_AUDIT.md` (extreme maintenance burden — capability status tracked here and in CHANGELOG).
- **Authoritative docs**: This file (build-continuity/roadmap), `ARCHITECTURE.md` (current system state), `README.md` (quickstart), `config/PIPELINE_REFERENCE.md` (config reference).
- **Supplementary**: `E2E_TEST_GUIDE.md`, `OPTEST.md`, `BUILD_VARIANTS.md`, `PIPELINE_QUALITY_IMPROVEMENTS.md`, `web/README.md`, `web/STYLE_GUIDE.md`.

---

## 10) Capabilities Audit

*Removed.* The per-capability status checklist was previously maintained in `CAPABILITIES_AUDIT.md` (deleted — extreme maintenance burden; line-number references stale within days of any code change). Current capability status is tracked in this document's roadmap (§6) and in `CHANGELOG.md`. Git history preserves the final snapshot.
