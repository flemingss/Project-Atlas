# Technical Debt & Code Review: Project Atlas

This repository implements **Project Atlas**, a sophisticated RAG (Retrieval-Augmented Generation) system with a multi-stage ingestion pipeline. The codebase is generally high-quality, modern (Python 3.11+, Pydantic v2, SQLAlchemy 2.0), and well-documented.

However, there are specific areas of technical debt and hidden dangers that should be addressed.

---

## 1. Technical Debt & Refactoring Targets

### A. The "God Object" Controller (`src/atlas/api_admin.py`) — ✅ Fully Remediated

~~This file is ~2,530 lines and violates the Single Responsibility Principle.~~

**Remediation (Phase 1):** Extracted into `src/atlas/admin/` sub-package:
- `_helpers.py` — Shared utilities (`group_count`, `ledger_summary`, `qdrant_*`, `parse_cursor`, `clean_scope_id`)
- `scope.py` — Tenant/Project/Corpus CRUD (9 endpoints)
- `looking_glass.py` — Monitoring & debugging (10+ endpoints, `_build_metrics`)
- `cleanup_rules.py` — Cleanup feedback + rule management (suggest, apply, dry-run, export, import, delete)

**Remediation (Phase 2):** Complete decomposition:
- `config.py` — Config effective, reload, restore-stock, validate-rules, config-versions CRUD
- `hitl.py` — HITL task lifecycle (list/create/complete/resume/skip/reject)
- `workflow.py` — Workflow runs, node-runs, artifacts CRUD
- `maintenance.py` — Orphan cleanup, adopt, dangling-run, doc active-version, reassociate-scope, doc delete
- `exports.py` — Doc/corpus/project/tenant export, scoped export, corpus import

`api_admin.py` reduced from **~2,530 to ~170 lines** — now a thin coordinator with only `db/reset` and `self-test` inline.

### B. Ingest Backend Complexity (`src/atlas/pipeline/ingest.py`) — ✅ Remediated

~~The `IngestNode` class contains complex branching logic in `process_doc_bytes()`.~~

**Remediation:** Implemented Strategy Pattern in `src/atlas/pipeline/parsers.py`:
- `DocumentParser` ABC with `DoclingParser`, `LayoutParser`, `VisionParser`, `FallbackParser`
- Factory function `build_parser(backend, ctx)` selects the right strategy
- `IngestNode` reduced from ~656 to ~280 lines

### C. Regex-Based LLM Cleaning — ✅ Remediated

~~The `strip_llm_artifacts` function and its regex constants lived inline in `refine.py`.~~

**Remediation:** Extracted to dedicated `src/atlas/pipeline/guardrails.py` module:
- `strip_llm_artifacts()` + 4 regex constants (`_PREAMBLE_PATTERNS`, `_POSTAMBLE_PATTERNS`, `_META_HEADING_RE`, `_CODE_FENCE_WRAPPER_RE`)
- `refine.py` imports from `guardrails` — zero duplication
- 36 existing tests updated to import from new module
- `refine.py` reduced from ~636 to ~500 lines

---

## 2. "Foot Guns" (Potential Dangers)

### A. Sectional Refinement Context Loss

*   **Location:** `src/atlas/pipeline/refine.py` → `refine_document_sectional`
*   **Risk:** This splits long documents into sections based on token counts (via `split_into_sections()`) to fit context windows, then processes each section *independently*. If a sentence or logical thought straddles a section boundary, the refinement model may hallucinate a fix or break the narrative flow because it lacks the adjacent context.
*   **Mitigation:** Implement sliding window overlaps or smart splitting that only breaks on markdown headers.

### B. Destructive Cleanup Rules

*   **Location:** `src/atlas/pipeline/cleanup_rules.py`
*   **Risk:**
    *   `_step_merge_hardwrapped`: Treats all non-blank-separated line breaks as formatting errors and merges them into paragraphs. In poetry, code blocks (if not detected perfectly), or specialized lists, this will destroy data.
    *   `_step_fix_numbered_headings`: Forces markdown header levels to match text numbering depth (e.g., `len("1.1.2".split("."))` → H3). If a document uses "1.1" as a top-level title, this logic will bury it in the hierarchy.
*   **Mitigation:** Ensure `merge_hardwrapped` is never enabled globally; it should be an opt-in rule per corpus.

### C. Judge Parsing Fragility — ✅ Remediated

~~The code relies on `key, _, value = line.partition(":")` to read LLM scores.~~

**Remediation:** Added `_extract_int()` static method with `re`-based extraction that handles bold markers, extra whitespace, and other markdown variations. Falls back gracefully on unparseable output.

---

## 3. Test Coverage Gaps — ✅ Remediated

### A. Pipeline Modules — Tests Added

| Pipeline Module | Test Coverage | Status |
| :--- | :--- | :--- |
| `judge.py` | 22 dedicated tests in `tests/test_judge.py` | ✅ **Covered** |
| `metadata.py` | 13 dedicated tests in `tests/test_metadata.py` | ✅ **Covered** |
| `orchestrator.py` | 6 dedicated tests in `tests/test_orchestrator.py` | ✅ **Covered** |
| `tokens.py` | 16 dedicated tests in `tests/test_tokens.py` | ✅ **Covered** |
| `refine.py` (RefineNode) | 36 artifact-stripping tests (`test_llm_artifact_stripping.py`) + 22 RefineNode tests (`test_refine_node.py`): preservation guardrail, section-count guardrail, sectional refinement, error handling, prompt building | ✅ **Covered** |
| `runner.py` | Partial coverage via `test_phase_refactors.py`, `test_pipeline_state.py` | **Partial** |

### B. Coverage Enforcement — ✅ Enabled

**Remediation:** Added `pytest-cov>=5.0` to dev dependencies and configured `--cov=atlas.pipeline --cov-report=term-missing --cov-fail-under=80` in `pyproject.toml`. Current coverage: **89.8%**.

---

## 4. Dependency Pinning — ✅ Remediated

~~All core dependencies in `pyproject.toml` use loose `>=` ranges without upper bounds.~~

**Remediation:** Generated `requirements.lock` and `requirements-dev.lock` via `pip-compile` for reproducible builds. Loose ranges in `pyproject.toml` remain for flexibility, but lock files pin exact versions for CI/production.

---

## 5. Summary

| Priority | Action | Status |
| :--- | :--- | :--- |
| **High** | Split `api_admin.py` into domain sub-modules | ✅ Done — `src/atlas/admin/` package (2,530 → 170 lines, 10 sub-modules) |
| **High** | Add tests for `judge.py`, `metadata.py`, `orchestrator.py`, `tokens.py`, `refine.py` | ✅ Done — 79 new tests |
| **High** | Enforce coverage thresholds in CI | ✅ Done — 80% threshold, currently 89.8% |
| **Medium** | Refactor `IngestNode` to Strategy Pattern | ✅ Done — `parsers.py` |
| **Medium** | Review `cleanup_rules` defaults | ✅ Verified safe — `merge_hardwrapped` is opt-in |
| **Medium** | Pin dependencies with a lock file | ✅ Done — `requirements.lock` / `requirements-dev.lock` |
| **Medium** | Harden Judge parsing | ✅ Done — `_extract_int()` regex parser |
| **Low** | Extract LLM output guardrails | ✅ Done — `pipeline/guardrails.py` |
