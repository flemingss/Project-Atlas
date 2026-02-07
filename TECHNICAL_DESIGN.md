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
- Enforce multi-tenancy (`tenant_id`, `project_id`)
- Provide a **HITL checkpoint** when automation cannot meet quality thresholds
- Export a repeatable “RAG package” (manifest + enriched markdown) for downstream systems

The intended operator is a **non-technical domain expert** who can run the appliance, ingest documents, and resolve HITL tasks without touching code.

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
- ✅ Settings via `pydantic-settings` (`src/atlas/settings.py`, `.env.example`)
- ✅ Postgres connection + schema creation (SQLAlchemy) for config versions only

### 3.2 Config management

- ✅ YAML defaults: `config/pipeline.yaml`, `config/models.yaml`
- ✅ DB-backed config snapshots + activation (table `config_versions`)
- ✅ Admin endpoints to view effective config and create/activate config versions

Notes:

- Config snapshots are whole payloads `{pipeline, models, _meta}` with stable hash.

### 3.3 RAG MVP (current runtime path)

- ✅ `POST /rag/ingest/text`
  - chunk via `atlas.rag.chunking.chunk_text` (simple paragraph chunking)
  - embed via `ModelRegistry.resolve('embed_model')` + provider `embed()`
  - upsert to Qdrant collection `atlas_chunks`
  - deterministic chunk IDs (doc_id + doc_version + content_hash + chunk_index)

- ✅ `POST /rag/search`
  - embed query via same embed provider
  - Qdrant vector search with must filters:
    - `tenant_id`
    - `project_id`
    - `is_finalized == true`

### 3.4 LLM provider abstraction

- ✅ Provider protocol `ILlmProvider` with `chat()` and `embed()`
- ✅ OpenAI-compatible provider (LM Studio typical) for chat+embeddings
- ✅ Deterministic embeddings provider for repeatable local tests (embeddings only)
- 🟨 Frontier providers are present in config shape (OpenAI/Anthropic) but not implemented

### 3.5 Vector store integration

- ✅ `QdrantStore` wrapper
- ✅ `ensure_collection()` validates dimension mismatch on existing collection (clear error)

### 3.6 Pipeline modules (scaffold)

- 🟨 Pipeline state machine and nodes exist under `src/atlas/pipeline/*`
- 🟨 Node logic is mostly placeholder (judge/refine/metadata do not call providers yet)
- 🟨 Pipeline orchestrator is not wired into the `/rag/*` endpoints
- ✅ No LangGraph dependency exists today

### 3.7 HITL

- 🟨 In-memory HITL task queue with priority logic exists (`src/atlas/hitl.py`)
- 🟨 No persistence; no API endpoints; no UI
- 🟨 “Push to Dify” is explicitly a placeholder

### 3.8 E2E and repeatability

- ✅ Unit tests and integration tests exist and pass in this repo
- ✅ Black-box E2E runner exists:
  - starts Docker infra (minimal compose)
  - starts the API
  - runs a suite of API scenarios (admin + rag)
  - uses deterministic embeddings automatically to avoid LM Studio dependency

---

## 4) Pivots and decisions incorporated

These are the directional changes discussed, reflected as **Planned** unless implemented.

### 4.1 Dify

- 🛠 Pivot: **Dify is not the default RC UI/HITL hub**.
- 🛠 Dify may remain optional (kept in compose for experiments), but the primary path is:
  - Atlas owns workflow state and HITL tasks
  - a purpose-built console provides monitoring and HITL interaction

Rationale:

- Appliance-grade repeatability favors fewer moving parts
- A general-purpose workflow UI tends to create “mysterious state” and provisioning complexity

### 4.2 LangGraph

- 🛠 Decision: LangGraph can be introduced **only if it reduces risk** (checkpointing/pausing/trace), but it is not required for the next RC step.
- 🛠 If introduced, treat it as an internal implementation detail behind stable Atlas contracts.

### 4.3 “Black box” operation

- 🛠 Target: keep the system black-box from the UI perspective.
- 🛠 Provide a minimal monitoring + HITL pane for operators.

---

## 5) Target end-state architecture (what “complete” looks like)

### 5.1 End-state modules

1) **Ingest subsystem**
   - 🛠 Docling integration for PDF/Office; store ground truth JSON
   - 🛠 Markdown projection with traceability fields

2) **Agentic pipeline (automation loop)**
   - 🛠 Judge with real provider calls + rubric + versions
   - 🛠 Refine with real provider calls + retry limits
   - 🛠 Metadata tiering with enforced caps
   - 🛠 Embeddings + chunking + commit integrated as first-class pipeline steps

3) **Durable workflow state**
   - 🛠 Document/job table(s): ingest runs, current node, timestamps, error codes
   - 🛠 Node-run trace table(s): inputs/outputs references, model/provider versions, durations
   - 🛠 Artifact store (filesystem first; DB stores references)

4) **HITL**
   - 🛠 Durable HITL task model (DB)
   - 🛠 Task lifecycle: pending → in_progress → resolved/skip/reject
   - 🛠 Re-run behavior: resolve edit → resume pipeline from a defined checkpoint

5) **Retrieval-time enhancements**
   - 💭 Hybrid search + rerank (see Fantasy Check for risk/alternatives)

6) **Professional output**
   - 🛠 Export “RAG package”:
     - manifest.json
     - enriched markdown chunks with YAML frontmatter
     - index describing doc/chunk relationships

7) **Appliance-grade operations**
   - 🛠 One-command bring-up
   - 🛠 Built-in self-test (E2E scenario)
   - 🛠 Auth + safe defaults (no default secrets in non-dev)
   - 🛠 Backups/reset/upgrade guidance

8) **Repo Looking Glass (corpus inspection)**
  - 🛠 Goal: operators can assess corpus health/state **without exporting packages**
  - 🛠 Scope: read-only inspection of documents/chunks/runs/HITL + storage/Qdrant health
  - 🛠 Non-goals: not a general-purpose UI platform; not a full labeling system; no “mysterious state”
  - 🛠 Minimum query surface (API-first; UI can be a thin layer over these):
    - corpus inventory: document counts, chunk counts, finalized/non-finalized, by tenant/project
    - document view: doc metadata, available doc_versions, version activation/finalization state
    - chunk preview: show stored chunk text + key metadata (doc_id, doc_version, source spans)
    - run ledger: list ingest runs + current status + last error code + timestamps
    - HITL queue: list tasks and link them to runs/docs
    - storage health: Qdrant collections + vector dim + point counts; artifact store presence

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
- 🛠 Add DB tables for:
  - HITL tasks
  - optional trace events (or references to artifact files)
- 🛠 Add API endpoints:
  - create/list/get/resolve HITL tasks
  - list job/runs + status
  - extend Looking Glass queries to be driven off the durable ledger (jobs/node_runs) instead of ad-hoc inspection

**Definition of done:** you can force a HITL task in tests, resolve it, and see the resulting committed chunks.

### Phase 3 — Wire the pipeline into the ingest path

- 🛠 Replace the current `/rag/ingest/text` “direct path” with a pipeline run (even if ingest is text-only initially)
- 🛠 Implement judge/refine/metadata nodes to actually call configured providers
- 🛠 Update E2E scenarios to cover:
  - “low score triggers refine then passes”
  - “max retries triggers HITL task”

**Definition of done:** the pipeline is the default path for ingest and is fully test-covered.

### Phase 4 — Docling ingestion for PDFs/Office

- 🛠 Implement Docling parsing and store ground truth
- 🛠 Build deterministic regression tests on sample documents

**Definition of done:** PDF ingestion is reliable enough for shakedown; failures produce actionable diagnostics.

### Phase 5 — Professional output + rollback

- 🛠 Export pipeline artifacts (manifest + enriched markdown)
- 🛠 Implement rollback at **doc_version granularity** (no supersedes chains in v1)

**Definition of done:** operators can export a consistent package and recover a prior version.

### Phase 6 — Retrieval-time upgrades (optional / scoped)

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
- Command: python scripts/retrieval_eval.py --api-url http://127.0.0.1:8080 --golden eval/retrieval_golden.example.json --out retrieval_report.json

---

## 7) Adjudicated “hard items” (decisions landed)

This section resolves the previously flagged “fantasy” items into explicit scope decisions.

### 7.1 Retrieval: “Hybrid search (BM25) + dense + rerank”

**Decision (v1/end-state staging):**

- ✅ v1 uses **vector-only retrieval + metadata filters** (tenant/project/is_finalized, plus future fidelity filters).
- 🛠 Hybrid keyword search and rerank are **explicitly deferred** until we have a measured failure mode that cannot be fixed by better chunking/metadata.

**Rationale:** hybrid/rerank turns Atlas into a search stack with tuning + eval + ops overhead. It is a great upgrade *when needed*, not a baseline requirement for an appliance RC.

### 7.2 Ingest quality: “Docling + OCR + tables/equations are consistently professional-grade”

**Decision (scope control):**

- 🛠 Atlas will define **supported ingest profiles** and expand them intentionally.
- 🛠 v1 PDF support targets: “extract best-available text + structure”, with diagnostics + HITL for hard pages.
- 🛠 “Perfect tables/equations across arbitrary PDFs” is **not a promise**; it is addressed by:
  - constrained doc profiles
  - regression corpus tests
  - clear failure modes + operator workflow (HITL)

### 7.3 Observability: “Checkpoints automatically become a great trace UI”

**Decision (queryable ledger first):**

- 🛠 Persist a small **run ledger** (jobs + node_runs) in Postgres as the source for UI queries.
- 🛠 Store large artifacts (docling JSON, prompt bodies, intermediate markdown) in the filesystem artifact store; DB stores references.
- 🛠 LangGraph checkpointing is optional and must not replace the ledger.

### 7.4 Versioning/rollback: “Supersedes chains + grace periods + rollback”

**Decision (simplify version semantics for v1):**

- 🛠 v1 rollback is implemented at **doc_version granularity**:
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
- Operators can ingest representative documents (text first; PDFs when Phase 4 lands)
- The system deterministically produces committed chunks and a package export
- When automation fails, operators have a HITL queue to resolve issues
- Every decision is traceable (which model/provider/config, which run, why it paused)

---

## 9) Known documentation deltas to fix (repo alignment)

- This document should be treated as the authoritative build-continuity doc; other docs should be adjusted to match.
