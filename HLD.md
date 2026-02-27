HLD: Adaptive Agentic Knowledge Runtime ("Project Atlas")

Status note (Feb 2026): This document captures the original high-level intent. The authoritative build-continuity plan (including current repo reality and explicit scope decisions) is `TECHNICAL_DESIGN.md`.

Key deltas vs original intent:
- LangGraph: optional only if it reduces risk; not required for the next RC.
- HITL UI: Dify is optional/experimental; the default direction is a purpose-built console (“Control Center”).
- Retrieval v1: vector-only + metadata filters; hybrid BM25/rerank deferred until a measured failure mode requires it.
- Rollback v1: doc_version granularity; deep supersedes-chain semantics deferred.
- Repo inspection: a “Repo Looking Glass” is planned so operators can assess corpus state without exporting.- Judge rubric: expanded from single 1–5 score to four dimensions (faithfulness, formatting, cohesion, hallucination_risk) in v0.4.0.
- Pipeline: Cleanup node added between Ingest and Judge; unified routing logic; retry/backoff on all external calls.
- Cleanup rules: v0.5.0 adds a config-driven cleanup rules engine (7 step handlers, first-match-wins, rule-tag routing including CLEANUP→HITL escalation).
- Cleanup feedback: operator feedback loop with CRUD API + metrics aggregation endpoint.
- Rule suggestion: v0.5.0 adds LLM-assisted rule suggestion (`POST /admin/cleanup-rules/suggest`) with heuristic fallback and deterministic provider support for CI.
- Cleanup rules import/export: `GET /admin/cleanup-rules/export` downloads active rules as YAML; `POST /admin/cleanup-rules/import` accepts YAML with replace/merge modes for portable rule management.
- Cleanup & Tuning UI: Admin tab card for viewing rules, submitting feedback, browsing metrics, invoking AI rule suggestion, and exporting/importing cleanup rules.
- Chunking: default strategy is semantic with automatic QA + fallback; chunk_with_fallback replaces direct calls.
- Docling health score: computed after every ingest to surface extraction quality.
- Refine guardrails: v0.6.0 adds content-safety guardrails (min_preservation_ratio, tightened prompt, refine_version v2) to prevent LLM content loss.
- Normalize boundary: v0.6.0 refactors normalize to formatting-only; page-number and noise stripping moved to cleanup builtins (`strip_page_numbers`, `strip_repetitive_lines`).
- Runner consolidation: v0.6.0 consolidates the pipeline runner (5 shared helpers, 37% line reduction) with all silent exceptions replaced by logged warnings.
1. System Vision

Project Atlas ingests heterogeneous domain documents and outputs a "Professional Grade" RAG package. The system is local-first (RTX 3090), idempotent, self-refining, and features Human-in-the-Loop (HITL) checkpoints.

2. Core Architecture: The Agentic Loop

Originally proposed: a State Graph (LangGraph) to manage the lifecycle of a document. Current implementation uses an internal pipeline scaffold; LangGraph remains optional.

Node Flow & Logic

Ingest (Docling):

Action: Convert PDF/Office to DoclingDocument JSON.

Ground Truth: Store the full JSON object as the structural source. Markdown is generated as a projection for LLM consumption.

Traceability: Store source_mime_type and parse_profile (e.g., pdf_scanned, pptx).

Versioning: Track docling_schema_version.

Judge (Llama 3.2 3B):

Action: Grade the Markdown projection (1–5) using an explicit few-shot rubric.

Explainability: Output a confidence_rationale and persist judge_version (prompt + model hash).

Refine (Llama 3.2 Vision):

Action: Triggered if Judge Score < 4.

Constraint: Max 2 retries then move to HITL.

Safety: Tag problematic chunks with fidelity_flag (e.g., partial, low_confidence).

Embeddings Module (Standalone):

Action: Decoupled component for vector generation.

Traceability: Store embedding_model and embedding_version in every chunk's metadata to support mixed-model migrations.

Metadata (Tiered Approach):

Tier 1 (Small Local): Standard tagging for 90% of chunks.

Tier 2 (70B / Frontier): Used for technical density or borderline Judge scores (3–4).

Cost Guardrail: Configurable hard cap of N Tier-2 chunks per document.

Chunking:

Action: Heading-aware transformation.

Context: Store parent_header_id, sibling_ids, and section_path (e.g., ["Chapter 1", "Thermal Dynamics"]).

Commit (Qdrant):

Action: Vectorize and store.

Idempotency: deterministic_id = hash(content_hash + doc_version).

State Management: Use is_finalized and supersedes_chunk_id. Superseded chunks are hidden from standard retrieval after a grace period.

3. Retrieval-Time Logic

The runtime path is optimized for high-precision fact retrieval:

Hybrid Search (deferred): Combine vector similarity (dense) with BM25 (keyword) search on content and section_path.

Filtering: Default filter on is_finalized: true and tenant_id. Optionally filter by fidelity_flag for high-stakes queries.

Rerank Step (deferred): Use a small local cross-encoder (e.g., BGE-Reranker) to re-score the top candidates using the rich metadata fields.

4. Concurrency & Resource Guard

vLLM Semaphore: Concurrency = 1 for "Heavy" tasks.

Privacy Guard: Default is_sensitive: true. Override required for API routing. Enforced per-tenant.

Frontier Fallback: Dynamic swing to API based on VRAM % and queue_depth.

5. Management, HITL & Diagnostics

Central Config: Thresholds (Judge cutoffs, VRAM limits, cache similarity) are centralized and versioned via pipeline_config_version.

Diagnostics: Structured error channel (e.g., DOC_PARSE_TIMEOUT, VLM_OCR_FAIL) and a "debug trace level" to capture intermediate prompts/responses.

HITL Hub (Pluggable): Originally Dify-based interface with a priority queue. Current direction: purpose-built console; Dify optional.

Multi-Tenancy: tenant_id and project_id required in all metadata.

6. Output: The "Professional RAG" Package

Enriched Markdown: Files with YAML frontmatter containing metadata, queries, and health metrics.

Manifest File: JSON record of origin URI, ingest time, embedding_model, and a health summary (% pages HITL, mean_judge_score).

7. Performance & Evaluation

Semantic Cache: Similarity > 0.98 to reuse existing metadata.

Cold-Start Mode: Aggressive first pass; subsequent passes use incremental sync.

Rollback Tool (re-scoped): rollback at doc_version granularity first; supersedes-chain semantics deferred.

Eval Hooks: Golden QA set for "shadow evaluation" of retrieval/answering quality. Unit tests per node to validate heading and parent/child logic.