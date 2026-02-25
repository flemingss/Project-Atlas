# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
