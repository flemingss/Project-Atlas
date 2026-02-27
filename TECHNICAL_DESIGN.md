# Project Atlas — Technical Design (Feb 2026)

This document is the build-continuity technical design for Project Atlas. It covers:

1) **Original intent** (from `HLD.md`)
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

The intended operator is a **non-technical domain expert** who can run the appliance via a **Streamlit-based operator UI**, ingest documents, and resolve HITL tasks without touching code.

---

## 2) Original HLD intent (baseline requirements)

The high-level design (`HLD.md`) describes an agentic loop:

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
  - uses Docling for PDF/Office parsing with guardrails (max bytes, max pages, timeout, quality gates)
  - persists Docling JSON ground truth + markdown projection as artifacts
  - falls back to text extraction when Docling is unavailable

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

- ✅ Provider protocol `ILlmProvider` with `chat()` and `embed()`
- ✅ OpenAI-compatible provider (LM Studio typical) for chat+embeddings
- ✅ Deterministic provider for repeatable local tests: `embed()` returns stable SHA-256-derived vectors; `chat()` returns heuristic responses for judge (score/rationale), refine (improved markdown), and metadata (JSON tags)
- 🟨 Frontier providers are present in config shape (OpenAI/Anthropic) but not implemented

### 3.5 Vector store integration

- ✅ `QdrantStore` wrapper
- ✅ `ensure_collection()` validates dimension mismatch on existing collection (clear error)

### 3.6 Pipeline modules

- ✅ Pipeline state machine (`PipelineStateManager`) and orchestrator (`PipelineOrchestrator`) under `src/atlas/pipeline/*`
- ✅ 11-node pipeline: INGEST → CLEANUP → JUDGE → REFINE → METADATA → EMBEDDINGS → CHUNKING → COMMIT (+ HITL, COMPLETED, FAILED)
- ✅ **Cleanup node** (`pipeline/cleanup.py`): deterministic markdown cleanup between Ingest and Judge. Five built-in transforms (normalise whitespace, strip broken links, repair heading hierarchy, strip trailing whitespace, static checks) plus five configurable builtin extraction-artifact fixes (`html_unescape`, `fix_ligatures`, `strip_zero_width_chars` — ON by default; `strip_page_numbers` — ON by default; `strip_repetitive_lines` — OFF by default; via `builtin_cleanup:` config section). Produces `CleanupResult`. Accepts optional `doc_context` and `config` to apply config-driven cleanup rules after built-in transforms.
- ✅ **Cleanup rules engine** (`pipeline/cleanup_rules.py`): declarative, first-match-wins rule engine for per-corpus / per-mime-type cleanup. Seven step handlers: `strip_lines_matching`, `rewrite_pattern`, `strip_headers_footers`, `normalize_headings`, `merge_hardwrapped_paragraphs`, `fix_bullets`, `html_unescape`. Rules configured in `pipeline.yaml` `cleanup_rules:` section. Rule tags (`hard_failure`, `suspicious_content`, `auto_fix_only`) influence routing decisions. `html_unescape` handler delegates to the shared builtin implementation.
- ✅ **Judge node** (`pipeline/judge.py`): multi-dimensional rubric — FAITHFULNESS, FORMATTING, COHESION, HALLUCINATION_RISK (each 1–5). Composite score = rounded mean. Legacy single-SCORE fallback preserved. Versioned `judge_version` (model + prompt hash).
- ✅ **Refine node** (`pipeline/refine.py`): configurable max retries, triggers HITL when exhausted. Content-safety guardrails: tightened system prompt ("MUST NOT summarise, condense, or omit"), `min_preservation_ratio` (default 0.6) rejects outputs shorter than 60% of input. Versioned `refine_version` v2.
- ✅ **Metadata node**: tiered (tier1 / tier2) with configurable per-doc tier2 cap
- ✅ **Unified routing** (`pipeline/routing.py`): `decide_next_step()` pure function centralises all branching (fail-fast on composite ≤ `fail_fast_score`, per-dimension floor checks, cleanup-rejudge when formatting is bad but content is OK, standard refine/HITL paths, rule-tag-aware cleanup routing — `hard_failure`→FAILED, `suspicious_content`→HITL). `PipelineStateManager.get_next_node()` delegates here.
- ✅ **Retry/backoff** (`retry.py`): `RetryConfig` + `async_retry()` / `sync_retry()` decorators. Applied to `openai_compat.py`, `qdrant_store.py`, `docling_adapter.py`. Config-driven per subsystem via `pipeline.yaml` `retry:` section.
- ✅ **Chunk QA + fallback** (`rag/chunk_qa.py`): post-chunking validation + automatic fallback (semantic→paragraph, hierarchical→paragraph). Bounds configurable via `pipeline.yaml` `chunking.qa:` section.
- ✅ **Docling health score** (`ingest/docling_health.py`): `compute_health()` evaluates extraction quality signals into a 1–5 composite score, stored on `PipelineContext`.
- ✅ Pipeline runner is wired into `/rag/ingest/*` endpoints (text + file) and executes the full agentic loop with cleanup, chunk QA fallback, and health scoring. Runner consolidated into ~996 lines with 5 shared helpers.
- ✅ No LangGraph dependency exists today

Notes:

- Prompts/parsing are v1 and will evolve; the pipeline contract is stable.
- Pipeline config (`pipeline.yaml`) includes `retry:`, `chunking.qa:`, `judge_dim_floors:`, `fail_fast_score`, `cleanup_rejudge`, and `cleanup_rules:` sections.

### 3.7 HITL

- ✅ Durable HITL tasks are persisted in Postgres (`hitl_tasks`) with a small CRUD/queue API under `/admin/hitl/*`
- 🟨 In-memory HITL queue exists (`src/atlas/hitl.py`) but is no longer the primary runtime path
- 🟨 “Push to Dify” remains explicitly a placeholder/optional experiment
### 3.8 Cleanup feedback & metrics

- ✅ **Cleanup feedback model** (`models.py`): `CleanupFeedback` table with tenant/project/corpus/doc/chunk scoping, category, description, source spans, run_id FK, and metadata JSON.
- ✅ **Feedback ledger** (`feedback_ledger.py`): CRUD helpers — `create_feedback`, `get_feedback`, `list_feedback`, `delete_feedback`, `feedback_category_counts`.
- ✅ **Cleanup feedback API** (`api_admin.py`): five endpoints under `/admin/cleanup-feedback` — create (POST, 201), list (GET, scoped), categories (GET, aggregation), get by ID, delete by ID.
- ✅ **Metrics aggregation API** (`api_admin.py`): `GET /admin/looking-glass/metrics` with optional tenant/project/corpus scoping. Returns workflow status distribution, node failure rates by node name, HITL escalation rates, auto-accepted counts, and cleanup-feedback category counts.
- ✅ **LLM-assisted rule suggestion** (`rule_suggester.py`): `suggest_cleanup_rule()` accepts sample markdown + issues, calls the LLM (via `ModelRegistry`), and returns `{rule_yaml, rationale}`. Heuristic fallback when LLM unavailable. Deterministic provider branch for CI.
- ✅ **Rule suggestion endpoint** (`api_admin.py`): `POST /admin/cleanup-rules/suggest` — resolves `chat_model` or `refine_model`, invokes `suggest_cleanup_rule()`, returns JSON suggestion.
- ✅ **Cleanup rules import/export** (`api_admin.py`): `GET /admin/cleanup-rules/export` downloads active rules as YAML. `POST /admin/cleanup-rules/import` accepts YAML with `replace` (overwrite all) or `merge` (add/update by name) modes. Both validate rules against the schema before applying.

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

- ✅ Streamlit-based operator UI (`ui/app.py`, `Dockerfile.ui`) for non-technical users
  - file upload (text, PDF/Office), corpus navigation, HITL task review
  - Looking Glass views (inventory, docs, chunk preview, run history)
  - doc export (full + lean), corpus export/import
  - admin controls (config management, DB reset, self-test trigger)
  - wired into `docker-compose.yml` (port 18501)
- ✅ Three-round UI polish completed (Round 3: locked page skeleton)
  - 4-file design system: `theme.py` (tokens), `styles.py` (CSS), `components.py` (primitives), `app.py` (logic)
  - locked skeleton: every tab renders header + scope strip + max 3 cards
  - operator vs admin surface separation (admin controls visually gated)
  - 7 tabs + Admin (token-gated): Home, Upload, Library, Search, Review, Versions & Export, History, Admin
  - Admin tab includes: Diagnostics, Data Management, Group Management, **Cleanup & Tuning** (active rules, feedback submission, feedback categories, pipeline metrics, AI rule suggestion)
  - one-primary-action-per-tab pattern; calm, workspace-centric microcopy

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
- ✅ Atlas owns workflow state and HITL tasks; a Streamlit-based operator UI provides monitoring and HITL interaction.

Rationale:

- Appliance-grade repeatability favors fewer moving parts
- A general-purpose workflow UI tends to create “mysterious state” and provisioning complexity

### 4.2 LangGraph

- 🛠 Decision: LangGraph can be introduced **only if it reduces risk** (checkpointing/pausing/trace), but it is not required for the next RC step.
- 🛠 If introduced, treat it as an internal implementation detail behind stable Atlas contracts.

### 4.3 “Black box” operation

- ✅ Target: keep the system black-box from the UI perspective.
- ✅ Minimal monitoring + HITL pane for operators is delivered via the Streamlit UI.

---

## 5) Target end-state architecture (what “complete” looks like)

### 5.1 End-state modules

1) **Ingest subsystem**
   - ✅ Docling integration for PDF/Office; store ground truth JSON
   - ✅ Markdown projection with traceability fields
   - ✅ PDF-specific guardrails (size, page count, timeout, quality gates)

2) **Agentic pipeline (automation loop)**
   - ✅ Deterministic cleanup node (5 built-in transforms + 5 configurable builtins) between Ingest and Judge
   - ✅ Config-driven cleanup rules engine (7 step handlers, first-match-wins, rule-tag routing)
   - ✅ Multi-dimensional judge (4 dimensions: faithfulness, formatting, cohesion, hallucination_risk)
   - ✅ Unified routing with fail-fast, per-dimension floor checks, cleanup-rejudge, rule-tag-aware escalation
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
  - ✅ Minimum query surface (API-first; Streamlit UI is a thin layer over these):
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
   - ✅ Admin UI "Cleanup & Tuning" card in Streamlit
9) **Operator UI**
   - ✅ Streamlit-based operator console (`ui/app.py`, containerized via `Dockerfile.ui`)
   - ✅ Covers: upload, corpus browsing, HITL review, export, admin controls, Looking Glass views
   - ✅ 4-file design system (theme / styles / components / app) with locked page skeleton
   - ✅ Operator vs admin separation; 7 tabs; one-primary-action-per-tab; calm workspace-centric tone

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
- ✅ **Phase 7E — Admin UI "Cleanup & Tuning" card**: Streamlit Admin tab card for viewing active rules, submitting feedback, browsing categories, viewing pipeline metrics, and invoking AI-assisted rule suggestion with inline YAML preview.

**Definition of done (7A-7E):** All items implemented and tested.

### Phase 8 — Content-safety & codebase refactoring (v0.6.0)

- ✅ **Phase 8A — Refine content-safety guardrails**: Tightened `REFINE_SYSTEM_PROMPT`, added `min_preservation_ratio` (default 0.6), post-refine length check rejects outputs < 60% of input. Bumped `refine_version` to v2. Fixed double system-prompt bug.
- ✅ **Phase 8B — Normalize/cleanup boundary**: Normalize refactored to formatting-only (whitespace/line-break normalization). `strip_noise_markdown` removed. Page-number stripping and repetitive-line removal moved to cleanup builtins (`strip_page_numbers` ON by default, `strip_repetitive_lines` OFF by default).
- ✅ **Phase 8C — Runner consolidation**: Five shared helpers extracted. Both ingest paths rewritten to use shared helpers. 37% line reduction (1572 → 996 lines). All silent `except: pass` replaced with `log.warning`. Hoisted inline imports to module top. Normalize tracked as a pipeline node run.
- ✅ **Phase 8D — Polish**: `html_unescape` dedup (cleanup_rules delegates to cleanup builtin). 40 new tests in `test_phase_refactors.py`.
- ✅ **Cleanup rules import/export**: `GET /admin/cleanup-rules/export` (YAML download) and `POST /admin/cleanup-rules/import` (replace/merge modes). UI export/import controls in operator console. 10 new tests in `test_cleanup_rules_import_export.py`.

**Definition of done (8A-8D + import/export):** 348 tests passing. Runner consolidated. Refine guardrails prevent content loss. Normalize is formatting-only. Cleanup rules portable via YAML.

### Phase 8 — Retrieval-time upgrades (optional / scoped)

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
- Command: python scripts/retrieval_eval.py --api-url http://127.0.0.1:18080 --golden eval/retrieval_golden.example.json --out retrieval_report.json

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
- Operators have a Streamlit-based UI for upload, corpus browsing, HITL review, and Looking Glass views

---

## 9) Known documentation deltas to fix (repo alignment)

- This document should be treated as the authoritative build-continuity doc; other docs should be adjusted to match.

---

## 10) Capabilities Audit

The per-capability status checklist (Wired / Partial / Deferred / Unknown), with code entry points and documentation sources, is maintained in **[`CAPABILITIES_AUDIT.md`](CAPABILITIES_AUDIT.md)**. That document is the central tracking location for advertised vs implemented features.
