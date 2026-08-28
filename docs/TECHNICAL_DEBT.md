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

---

## 6. Open debt — carried forward from the 2026-08-28 agent scan

A read-only multi-agent scan was run against the repo on 2026-08-28. Every
claim was re-verified against source before being accepted; the items below
are the ones that survived and remain **open**. Resolved items from the same
scan are recorded in `WORKLOG.md` rather than duplicated here.

Verdict key: `CONFIRMED` — reproduced from source · `PARTIAL` — real, but the
framing or severity in the original report was off.

### A. Page-cap asymmetry between the two parsers — CONFIRMED

*   **Location:** `src/atlas/settings.py` (`atlas_pdf_max_pages = 2000`) vs
    `src/atlas/ingest/pdf_parser.py`
*   **Risk:** Docling refuses documents over `atlas_pdf_max_pages`; the layout
    parser enforces **no cap at all** and buffers every page. The two paths
    therefore disagree about what is too big, so raising the setting to reach
    the 3,000-page target silently shifts the failure from a clean rejection
    to unbounded memory growth.
*   **Note:** The cap itself is a setting, not a code change — the original
    report overstated this. The asymmetry is the real defect.
*   **Mitigation:** Enforce the same cap in the layout parser, and stream page
    handling there the way commit now streams chunks.

### B. `ATLAS_ENV` defaults to `dev` in the production compose — CONFIRMED

*   **Location:** `docker-compose.yml` (`ATLAS_ENV: ${ATLAS_ENV:-dev}`)
*   **Risk:** A production stack brought up without an explicit `ATLAS_ENV`
    runs with dev semantics.
*   **Note:** The same report flagged the hardcoded local Postgres password
    here as a security issue. On a single-host appliance that is low risk; the
    environment default is the item worth fixing.

### C. Coverage gate covers only `atlas.pipeline` — PARTIAL

*   **Location:** `pyproject.toml` (`--cov=atlas.pipeline`)
*   **Note:** Whole-package coverage would mostly add noise. Extending it to
    `atlas.vlm_ingest` is the change that would actually have caught the
    session-lifecycle defects fixed on 2026-08-28.

### D. Error bodies leak `str(e)` — CONFIRMED

*   **Location:** `src/atlas/api_rag.py` (502 handlers)
*   **Risk:** Upstream exception text reaches the client, which can disclose
    internal hostnames and provider detail.

### E. No explicit ruff `select` — CONFIRMED (reframed)

*   **Location:** `pyproject.toml`
*   **The original claim — that the `ignore` list is therefore hollow — is
    wrong**, and was tested directly: ruff's *default* rule set already
    includes `B`, `C4`, `S110`, `BLE001` and `EXE002`, so every registered
    ignore suppresses a genuinely enabled rule.
*   **The real issue** is that with no `select`, the enforced rule set is
    whatever the installed ruff version happens to default to, so an upgrade
    can silently widen or narrow the gate. Evidence it already bites:
    `line-length = 100` is configured while `E501` is not enabled, so nothing
    enforces it.
*   **Mitigation:** Pin an explicit `select` for reproducibility.

### F. Lower-priority items — CONFIRMED

*   **Container runs as root** — no `USER` directive in the `Dockerfile`.
*   **`personal_configs/pipeline.yml` is tracked** (since `4a275ea`). Scanned:
    **no secrets**, consistent with the git-history audit. Hygiene, not
    exposure.
*   **`/thumbnails` renders every page synchronously** and is unpaginated —
    use headless mode past a few hundred pages.
*   **The session cache can hold up to 50 source PDFs in RAM.** Bounded by LRU
    and cold release, and only reachable with 50 concurrent sessions.

### Rejected outright

Nothing else from the scan was accepted. Two items were self-marked as
hypotheses by the scanning agents and both needed exactly the verification
they asked for — one (the ruff claim, §E above) turned out wrong.

---

## 7. Ingest stack — Docling

### A. Docling is 53 releases behind — OPEN

*   **Pinned:** `2.76.0` (`requirements.lock`). **Latest:** `2.123.0`.

**What was actually established, and what was not.**

*   Installing `2.123.0` into the existing image — whether by
    `pip install --upgrade docling` or by exact pin — leaves a **broken
    package tree**: `docling.pipeline` present, `docling.document_converter`
    gone. The adapter then reports "Docling is not installed" and every parse
    fails. Measured with `scripts/ingest_quality.py`: both fixtures went from
    100% recall to a hard failure.
*   Installing intermediate versions *in sequence* (2.85 → 2.100 → 2.114 →
    2.120.2 → 2.123.0) ended with the adapter's imports working. That is not
    evidence the target version is fine — it means the chain happened to drag
    transitive dependencies (`docling-core` and friends) to compatible
    versions along the way. Do not read it as a green light.
*   **Not established:** whether a genuinely clean resolve of `2.123.0` — all
    dependencies solved together, as `pip-compile` would — parses correctly
    and at what quality. Nobody has run that.

**The only valid way to evaluate the upgrade** is therefore to bump the pin in
`pyproject.toml`, regenerate both lock files, rebuild the image, and then gate
on measurement:

```
python scripts/ingest_quality.py --input-dir samples --out baseline.json
#  ... bump pin, pip-compile, rebuild image ...
python scripts/ingest_quality.py --input-dir samples --out after.json \
    --baseline baseline.json     # non-zero exit on regression
```

Never upgrade Docling inside a running container — the result looks like a
missing dependency, not a broken upgrade.

### B. Docling failures are reported as "not installed" — OPEN

*   **Location:** `src/atlas/ingest/docling_adapter.py`
*   Any exception while importing Docling — a broken half-upgrade, a missing
    transitive dependency, a corrupt install — is converted to
    `DoclingUnavailableError` ("Docling is not installed"). The underlying
    cause is now attached to the diagnostic, but the operator-facing message
    still points at the wrong problem, and `backend: auto` then falls back to
    the layout parser and *succeeds* with lower-quality output. A silent
    quality drop is the worst version of this failure.
*   This is the same swallow-and-mislead shape as the `huggingface_hub`
    kwarg bug (see `WORKLOG.md`, 2026-08-28) and as a `TypeError` from a
    changed adapter signature surfacing as `DOC_PARSE_FAILED`.

### C. Table structure recognition: cost and fidelity — RESOLVED, documented

*   `pdf_parser.table_extraction` was documented in `pipeline.yaml.example`
    and **read by nothing** — setting it had no effect. Now wired through to
    Docling's `do_table_structure`, with a test that fails if it goes inert.
*   Measured on a 3x3 ruled table: **on** ~4.5s and real columns; **off**
    ~0.65s with the whole table collapsed onto one line.
*   TableFormer **normalises cell text** — it rewrites `1E-11` as `1e-11`.
    Table content is not reproduced byte-for-byte, so never compare it
    exactly.
