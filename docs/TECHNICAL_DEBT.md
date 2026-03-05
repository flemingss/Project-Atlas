# Technical Debt & Code Review: Project Atlas

This repository implements **Project Atlas**, a sophisticated RAG (Retrieval-Augmented Generation) system with a multi-stage ingestion pipeline. The codebase is generally high-quality, modern (Python 3.11+, Pydantic v2, SQLAlchemy 2.0), and well-documented.

However, there are specific areas of technical debt and hidden dangers that should be addressed.

---

## 1. Technical Debt & Refactoring Targets

### A. The "God Object" Controller (`src/atlas/api_admin.py`) — ✅ Remediated

~~This file is ~2,530 lines and violates the Single Responsibility Principle.~~

**Remediation:** Extracted into `src/atlas/admin/` sub-package:
- `_helpers.py` — Shared utilities (`group_count`, `ledger_summary`, `qdrant_*`, `parse_cursor`, `clean_scope_id`)
- `scope.py` — Tenant/Project/Corpus CRUD (9 endpoints)
- `looking_glass.py` — Monitoring & debugging (10+ endpoints, `_build_metrics`)
- `cleanup_rules.py` — Cleanup feedback + rule management (suggest, apply, dry-run, export, import, delete)

`api_admin.py` reduced from ~2,530 to ~1,310 lines. Remaining endpoints (config, workflow runs, HITL, doc versions, maintenance, exports) stay in the coordinator.

### B. Ingest Backend Complexity (`src/atlas/pipeline/ingest.py`) — ✅ Remediated

~~The `IngestNode` class contains complex branching logic in `process_doc_bytes()`.~~

**Remediation:** Implemented Strategy Pattern in `src/atlas/pipeline/parsers.py`:
- `DocumentParser` ABC with `DoclingParser`, `LayoutParser`, `VisionParser`, `FallbackParser`
- Factory function `build_parser(backend, ctx)` selects the right strategy
- `IngestNode` reduced from ~656 to ~280 lines

### C. Regex-Based LLM Cleaning (`src/atlas/pipeline/refine.py`)

The `strip_llm_artifacts` function uses a set of organized regex pattern groups to remove conversational chatter from LLM outputs:

| Pattern | Purpose |
| :--- | :--- |
| `_PREAMBLE_PATTERNS` | Removes conversational openers (e.g., "Here is", "Sure,", "As an AI") |
| `_POSTAMBLE_PATTERNS` | Removes conversational closers (e.g., "Let me know", "Feel free") |
| `_META_HEADING_RE` | Strips injected meta-commentary headings (e.g., "## Summary of Changes") |
| `_CODE_FENCE_WRAPPER_RE` | Unwraps content from bare markdown code fences |

*   **Tech Debt:** As models change (e.g., DeepSeek vs. Llama 3), they will invent new ways to be chatty, making this a perpetual maintenance burden.
*   **Fix:** Move this to a dedicated "Output Guardrail" module rather than hiding it inside the Refine node, or switch to structured output (JSON schema) enforcement where the model supports it.

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
| `refine.py` (RefineNode) | Only `strip_llm_artifacts` tested (`test_llm_artifact_stripping.py`); no tests for `RefineNode`, guardrails, or sectional refinement | **Partial** |
| `runner.py` | Partial coverage via `test_phase_refactors.py`, `test_pipeline_state.py` | **Partial** |

### B. Coverage Enforcement — ✅ Enabled

**Remediation:** Added `pytest-cov>=5.0` to dev dependencies and configured `--cov=atlas.pipeline --cov-report=term-missing --cov-fail-under=80` in `pyproject.toml`. Current coverage: **88%**.

---

## 4. Dependency Pinning — ✅ Remediated

~~All core dependencies in `pyproject.toml` use loose `>=` ranges without upper bounds.~~

**Remediation:** Generated `requirements.lock` and `requirements-dev.lock` via `pip-compile` for reproducible builds. Loose ranges in `pyproject.toml` remain for flexibility, but lock files pin exact versions for CI/production.

---

## 5. Summary

| Priority | Action | Status |
| :--- | :--- | :--- |
| **High** | Split `api_admin.py` into domain sub-modules | ✅ Done — `src/atlas/admin/` package (2,530 → 1,310 lines) |
| **High** | Add tests for `judge.py`, `metadata.py`, `orchestrator.py`, `tokens.py` | ✅ Done — 57 new tests |
| **High** | Enforce coverage thresholds in CI | ✅ Done — 80% threshold, currently 88% |
| **Medium** | Refactor `IngestNode` to Strategy Pattern | ✅ Done — `parsers.py` |
| **Medium** | Review `cleanup_rules` defaults | ✅ Verified safe — `merge_hardwrapped` is opt-in |
| **Medium** | Pin dependencies with a lock file | ✅ Done — `requirements.lock` / `requirements-dev.lock` |
| **Medium** | Harden Judge parsing | ✅ Done — `_extract_int()` regex parser |
| **Low** | Extract LLM output guardrails | Deferred — low risk, no breakage observed |
