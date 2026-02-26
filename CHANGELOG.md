# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-02-26

### Added
- **Config commit guardrails** — stock `.example` config pattern: `pipeline.yaml.example` and `models.yaml.example` are tracked in git; live config files are gitignored. Prevents operator-local settings from leaking into commits.
- **Cleanup rules schema validation** (`src/atlas/startup_validation.py`): `validate_cleanup_rules()` checks rule names, step kinds, regex compilation, match keys, and structural integrity. Called at startup; also exposed as `POST /admin/config/validate-rules`.
- **Restore stock config** (`POST /admin/config/restore-stock`): Copies `.example` → live config files and reloads. UI card in Admin → Danger Zone.
- **Apply cleanup rule via API** (`POST /admin/cleanup-rules/apply`): Validates a rule YAML string and appends it to the effective config via a new DB config version. No container restart required.
- **Remove cleanup rule** (`DELETE /admin/cleanup-rules/{name}`): Removes a named rule and creates a new DB config version.
- **Pre-commit hook** (`scripts/pre_commit_config_check.py`): Blocks commits that accidentally stage divergent live config files.
- **Rule suggestion sanitization** (`src/atlas/rule_suggester.py`): AI-suggested rules are now validated against the schema before display; `validation_errors` list and warning appended to response.
- 13 new cleanup-rules schema validation tests in `test_startup_validation.py`.
- **Config-driven cleanup rules engine** (`src/atlas/pipeline/cleanup_rules.py`): Declarative, first-match-wins rule engine for per-corpus / per-mime-type markdown cleanup. Six step handlers: `strip_lines_matching`, `rewrite_pattern`, `strip_headers_footers`, `normalize_headings`, `merge_hardwrapped_paragraphs`, `fix_bullets`. Rules declared in `pipeline.yaml` `cleanup_rules:` section.
- **Cleanup rules integration** (`src/atlas/pipeline/cleanup.py`): `CleanupNode.clean()` now accepts optional `doc_context` and `config` parameters. After built-in transforms, matches and applies the first matching rule from config. Fully backwards compatible — omitting the new params returns identical results.
- **Rule-tag-aware routing** (`src/atlas/pipeline/routing.py`): `decide_next_step()` reads `rule_tags` from cleanup results. `hard_failure` tag → FAILED, `suspicious_content` tag → HITL escalation, other tags or no tags → standard cleanup→judge path.
- **CLEANUP→HITL transition** (`src/atlas/pipeline/state.py`): Added HITL to valid transitions from CLEANUP node to support rule-tag-based escalation.
- **Cleanup feedback model** (`src/atlas/models.py`): New `CleanupFeedback` table (10th table) with tenant/project/corpus/doc/chunk scoping, category, description, source spans, run_id FK, and metadata JSON.
- **Feedback ledger** (`src/atlas/feedback_ledger.py`): CRUD helpers for cleanup feedback — `create_feedback`, `get_feedback`, `list_feedback`, `delete_feedback`, `feedback_category_counts`.
- **Cleanup feedback API** (`src/atlas/api_admin.py`): Five new endpoints — `POST /admin/cleanup-feedback` (201), `GET /admin/cleanup-feedback`, `GET /admin/cleanup-feedback/categories`, `GET /admin/cleanup-feedback/{id}`, `DELETE /admin/cleanup-feedback/{id}`.
- **Metrics aggregation API** (`src/atlas/api_admin.py`): `GET /admin/looking-glass/metrics` endpoint with optional tenant/project/corpus scoping. Returns workflow status distribution, node failure rates, HITL escalation rates, auto-accepted counts, and cleanup-feedback category counts.
- **Pipeline config expansion** (`config/pipeline.yaml`): new `cleanup_rules: []` section with commented examples for PDF scanned defaults, legal header stripping, and catch-all rules.
- **Extended `CleanupResult`** (`src/atlas/schemas.py`): Four new fields — `rules_applied`, `rules_failed`, `fix_counts`, `rule_tags` — all defaulting to empty via `field(default_factory=...)`.
- 44 new tests across 3 new test files: `test_cleanup_rules.py` (34), `test_cleanup_feedback.py` (7), `test_metrics_aggregation.py` (3).
- **LLM-assisted rule suggestion** (`src/atlas/rule_suggester.py`): On-demand AI module that accepts sample markdown + observed issues and produces a suggested cleanup rule (YAML). Includes heuristic fallback when no LLM is available. Deterministic provider branch added for CI-safe testing.
- **Rule suggestion API** (`src/atlas/api_admin.py`): `POST /admin/cleanup-rules/suggest` endpoint — calls configured LLM (or refine_model fallback) and returns `{rule_yaml, rationale}`.
- **Admin UI "Cleanup & Tuning" card** (`ui/app.py`): New card in the Admin tab — view active cleanup rules, submit cleanup feedback, browse feedback categories, view pipeline metrics, and invoke AI-assisted rule suggestion with inline YAML preview.
- 13 new tests in `test_rule_suggestion.py`: unit tests for deterministic provider branch, heuristic fallback (6 scenarios), `suggest_cleanup_rule()` function, and API endpoint integration.

### Changed
- `CleanupNode.clean()` signature extended to accept `doc_context: dict | None` and `config: dict | None` (backwards compatible).
- `PipelineOrchestrator._process_cleanup()` now passes doc context (tenant_id, project_id, corpus_id, source_mime_type, source_uri) and effective config to cleanup.
- Routing logic extended: CLEANUP node now reads `rule_tags` before standard cleanup→judge transition.
- State machine: CLEANUP valid transitions expanded from `[JUDGE, FAILED]` to `[JUDGE, HITL, FAILED]`.
- Test count: **265 passed** (up from 252 after Phases 7A-7C, up from 208 in v0.4.0).

## [0.4.0] - 2026-02-28

### Added
- **Pipeline resilience — retry/backoff** (`src/atlas/retry.py`): `RetryConfig` dataclass, `async_retry()` and `sync_retry()` decorators with exponential backoff. Config-driven per subsystem (`llm`, `vectorstore`, `docling`) via `pipeline.yaml` `retry:` section.
- **Chunk QA + fallback** (`src/atlas/rag/chunk_qa.py`): post-chunking validation (`validate_chunks`) with configurable bounds (min/max tokens, min chunks). Automatic fallback chain (`chunk_with_fallback`): semantic→paragraph, hierarchical→paragraph.
- **Cleanup node** (`src/atlas/pipeline/cleanup.py`): deterministic markdown cleanup inserted between Ingest and Judge. Five transforms: normalise whitespace, strip broken links, repair heading hierarchy, strip trailing whitespace, static checks. `CleanupResult` dataclass in `schemas.py`.
- **Multi-dimensional judge rubric** (`src/atlas/pipeline/judge.py`): expanded from single 1–5 score to four dimensions — FAITHFULNESS, FORMATTING, COHESION, HALLUCINATION_RISK. Composite score = rounded mean. Legacy single-SCORE fallback preserved.
- **Docling health score** (`src/atlas/ingest/docling_health.py`): `compute_health()` evaluates extraction_method, content_volume, rotation, text_as_shapes signals into a composite 1–5 `health_score`. Called after every ingest.
- **Unified routing function** (`src/atlas/pipeline/routing.py`): `decide_next_step()` pure function with `RoutingDecision` frozen dataclass. Supports fail-fast (composite ≤ threshold), cleanup-rejudge (formatting bad but content OK), per-dimension floor checks, standard refine/HITL paths.
- **Fidelity mode search filter** (`src/atlas/api_rag.py`): `SearchRequest.fidelity_mode` param (`verified` | `verified+partial` | `all`) adds a Qdrant filter on `fidelity_flag`.
- **Pipeline config expansion** (`config/pipeline.yaml`): new `retry:` section, `chunking.qa:` section, `judge_dim_floors:` per-dimension thresholds, `fail_fast_score`, `cleanup_rejudge` toggle.
- 80 new tests across 5 new test files: `test_retry.py` (14), `test_chunk_qa.py` (9), `test_cleanup.py` (15), `test_docling_health.py` (15), `test_routing.py` (21). Plus additions to existing test files.

### Changed
- Pipeline flow is now **Ingest → Cleanup → Judge → Refine → Metadata → Embeddings → Chunking → Commit** (11 nodes including HITL, COMPLETED, FAILED).
- `PipelineNode` enum now includes `CLEANUP`; transitions updated accordingly.
- `PipelineStateManager.get_next_node()` delegates to `routing.decide_next_step()`.
- `JudgeResult` schema gains `sub_scores: dict[str, int]` for per-dimension scores.
- `DeterministicProvider` updated to emit multi-dimensional judge output.
- `openai_compat.py` provider methods wrapped with `async_retry`.
- `qdrant_store.py` mutating operations wrapped with `sync_retry`.
- `docling_adapter.py` conversion wrapped with `sync_retry`.
- Both chunking sites in `pipeline/runner.py` replaced with `chunk_with_fallback()`.
- Both ingest paths (text + file) now call `compute_health()` and store Docling health.
- Test count: **208 passed** (up from 128 baseline pre-CR).

## [0.3.0] - 2026-02-26

### Added
- Admin API: corpus export/import, doc active-version management, DB reset, self-test endpoint.
- `export_package` module for doc-level and corpus-level export (full + lean formats).
- `startup_validation` module with pre-flight checks for DB, Qdrant, and artifact store.
- E2E scenario runner (`src/atlas/e2e/scenarios.py`).
- UI design-system components: `scope_strip`, `card_header`, `tab_header`, `admin_section`, `secondary_button`.
- UI layout plan document (`ui/UI_LAYOUT_PLAN.md`).

### Changed
- UI Round 3 polish: locked single-page skeleton (header + scope strip + max 3 cards per tab).
- Merged Export tab into "Versions & Export"; reduced tab count to 7 (Home, Upload, Library, Search, Review, Versions & Export, History).
- Operator vs admin surface separation: admin-only controls visually gated with dashed border.
- One-primary-action-per-tab pattern across all tabs.
- Microcopy overhaul: "Make searchable" (not "Ingest"), per-tab subtitles, calm workspace-centric tone.
- Card-level design language with consistent `card_header` (title + caption) pattern.
- Sidebar restructured: admin tools visually separated, auth-gated when no token.
- `Dockerfile` and `Dockerfile.ui` refinements; compose dev overlay cleanup.
- `api_admin.py` expanded with corpus and version management endpoints.
- `corpus_package.py` and `models.py` updated for export/import workflows.

## [0.2.0] - 2026-02-08

### Added
- Streamlit Operator Console UI overlay (Upload/Search/History/HITL/Export).
- In-UI Diagnostics with downloadable JSON log bundle.

### Changed
- Improved `/rag/ingest/file` handling for text uploads (plain/markdown bytes) and MIME guessing for octet-stream uploads.
- Minor local-dev ergonomics (compose hygiene, port adjustments) and docs updates.
