# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Version numbering note: the last tag is `v0.8.0` and `pyproject.toml` now reads
`0.8.0` to match it (it had been left at `0.7.2`; the `0.7.3-dev` section below
was never released under that number). The work in this section has no version
assigned yet — pick the next version and bump both when it ships.

### Removed (2026-08-27, compose consolidation)
- **Compose surface consolidated to two stacks — dev and prod.** Deleted
  `docker-compose.e2e.yml`, `docker-compose.optest.yml`,
  `docker-compose.slim.yml`, `Dockerfile.slim`, `scripts/optest.ps1`,
  `scripts/e2e_runner.py`, `OPTEST.md`, `BUILD_VARIANTS.md`, and
  `E2E_TEST_GUIDE.md`. The e2e/optest stacks predate the embeddings sidecar
  and LLM profiles (their Atlas service had no `embeddings` dependency, so
  ingest could not work against current code), and the slim variant was an
  unused fork of the image spec. `scripts/e2e_scenarios.py` survives and runs
  against the live dev stack (see README "E2E Scenario Tests"). Everything
  else is recoverable from git history if a CI harness is revived.

### Fixed (2026-08-27, hardening round)
- **`/rag` endpoints carry the admin auth posture** (`src/atlas/api_rag.py`):
  ingest (write path) and search (returns document content) were fully
  anonymous. They now use the same non-strict dependency as `/admin` — open
  in dev (bypass or no token configured), token-required otherwise. The SPA
  already sends the header.
- **Upload size limits enforced** on `POST /rag/ingest/file` and VLM
  `start-upload` (413 above `ATLAS_PDF_MAX_BYTES`); previously the cap was
  only checked deep in the Docling adapter, and the VLM path had no check at
  all.
- **Bulk VLM processing is cancellable**: discarding the session (DELETE) now
  stops the server-side loop at the next page boundary — previously the loop
  kept spending VLM tokens for the rest of the document with no way to stop
  it short of restarting the API.
- **Double-start guard on `process-all`** (409 when already processing), with
  status recovery so a cancelled or all-failed loop never wedges the session
  in PROCESSING; failed pages are retryable on re-run.
- **Startup reconciliation of orphaned runs** (`src/atlas/api.py`): Atlas is
  single-process, so any WorkflowRun still 'running' at startup was
  interrupted by a crash/restart; they are now marked failed
  ("interrupted by API restart") instead of sitting in 'running' forever.

### Fixed (2026-08-27, long-job durability)
- **VLM session TTL is now activity-based** (`src/atlas/vlm_ingest/session.py`):
  eviction keyed off `created_at` with a 1-hour TTL, so any bulk run longer
  than an hour could be evicted mid-job (by the maintenance loop or by another
  session's `create()`), destroying all VLM output. TTL now measures
  inactivity: status polls and page progress both refresh it, so attended and
  headless multi-hour runs (2,000+ pages ≈ 9–50 h at observed 17–90 s/page)
  survive; only sessions untouched for a full TTL window are evicted.
- **Page results checkpoint to disk as they complete**
  (`artifacts/vlm_sessions/<sid>/page_NNNN.md` + `session.json` with the
  render config): sessions are in-memory, so an API restart mid-run used to
  lose every processed page. A crash now loses at most the in-flight page;
  completed output is salvageable from the checkpoint dir (re-ingestable via
  Import, or headless re-run from the saved config).
- **Qdrant upserts batched at 512 points** (`vectorstore/qdrant_store.py`):
  a single upsert with thousands of points (3,000 pages ≈ ~6k chunks) risks
  the REST payload cap and retries the whole set on one transient failure.

### Fixed (2026-08-27, operator testing round 1)
- **Embeddings batched client-side** (`src/atlas/llm/openai_compat.py`): `embed()`
  sent every text in one request; the TEI sidecar rejects >32 inputs per call
  (its default `--max-client-batch-size`) with a 422, so any real document
  failed at commit (first hit: an 11-page datasheet → 57 chunks). Now batches
  in slices of 32 with per-batch retry; order preserved.
- **Embeddings model revision pinned** (`docker-compose.yml`): the nomic repo's
  HEAD ("v5 Transformers") regenerated `config.json` with both
  `max_position_embeddings` and `n_positions`; TEI's serde parser treats them
  as aliases of one field and crash-loops. Pinned `--revision e5cf08a` — which
  also protects the corpus from a silently swapped embedder.
- **VLM commit marks its run failed** (`src/atlas/api_vlm_ingest.py`): when the
  pipeline feed after a VLM commit throws, the WorkflowRun was left `running`
  forever; it is now marked `failed` with the error message.
- **Maintenance orphan scan tolerates missing collection** (`src/atlas/api.py`):
  scrolling `atlas_chunks` before first commit 404'd and logged a traceback
  every cycle; now treated as "nothing to scan".
- **API version reported from package metadata** (`src/atlas/api.py`): was
  hardcoded `0.1.0` in FastAPI and in the dashboard; `GET /` now exposes the
  installed version and the dashboard reads it. (Also removed a stale
  `src/project_atlas.egg-info` from an old editable install that shadowed the
  installed 0.8.0 metadata via the bind mount.)
- **SPA: Import/Paste sent `doc_name`, backend requires `doc_id`** — both
  ingest forms 422'd; they now send `doc_id` (Import falls back to the
  filename), and the client type marks `doc_id` required.
- **SPA: bulk VLM progress was blind** (`vlm-ingest-store.ts`): the progress
  bar computed from local page state that only per-page mutations updated, so
  server-side `process-all` showed 0% until it finished. The 5s session poll
  now syncs server page statuses into the wizard (never clobbering
  operator-edited markdown).
- **SPA: VLM session resume** — the wizard now persists the backend session id
  (localStorage) and re-attaches on load, landing on the step matching the
  server's state; `?vlm_session=<id>` deep-links are supported. A page refresh
  no longer orphans a running ingest (the backend loop always survived it).

### Added (2026-08-27, operator testing round 1)
- **"LLM configuration" card** on Admin → Health: active profile, config
  source/hash, and the role → provider → model table with ZDR/local-sidecar
  privacy badges, from `/admin/config/effective` (previously fetched by no
  page).

### Added (2026-08-27, compose consolidation)
- **`COMPOSE_FILE` in `.env`/`.env.example`**: bare `docker compose` now means
  the dev stack (base + dev overlay + devcontainer overlay); prod is an
  explicit `docker compose -f docker-compose.yml`.
- **`scripts/flush.ps1`**: host-side data flush between testing rounds —
  truncates all `atlas` DB tables, deletes every Qdrant collection, empties
  `artifacts/`, restarts `atlas-api`. Leaves containers, schema, and the
  embeddings weight cache intact.

### Added
- **LLM profiles** (`src/atlas/llm/profiles.py`, `config/models.yaml`): two generation postures selected by `ATLAS_LLM_PROFILE` or `active_profile` in `models.yaml`.
  - `local` — LM Studio or any LAN OpenAI-compatible server.
  - `api` (default) — OpenRouter.
  - A profile is a patch over **both** `models.yaml` and `pipeline.yaml`: it moves model ids *and* the tuning that has to travel with them (context budget, section size, judge budget). Swapping only the model ids would leave the pipeline sectioning documents that now fit whole. There is deliberately no "hybrid" profile — override a single role instead.
- **Embeddings sidecar** (`docker-compose.yml`): CPU `ghcr.io/huggingface/text-embeddings-inference:cpu-1.5` service serving `nomic-ai/nomic-embed-text-v1.5` (768-dim), reachable as `http://embeddings:80` on the compose network and published to host port **18090** for debugging (18080 is deliberately avoided — `docker-compose.optest.yml` publishes Atlas there). Weights cache in the `atlas_embeddings_cache` volume; the healthcheck allows a 180s cold start. `ATLAS_EMBEDDINGS_BASE_URL` points Atlas at it.
  - **Embeddings are pinned across profiles and cannot be profile-switched**, enforced in `profiles.py`. A vector search only returns meaningful results when queries and documents were embedded by the same model; swapping the embedder under an existing corpus corrupts retrieval, and when the replacement has the same dimension it does so *silently*, because Qdrant cannot detect it. Changing the embedding model means re-indexing everything.
  - Ingest and search no longer depend on LM Studio being up under either profile.
- **Zero-data-retention enforcement** (`src/atlas/llm/openai_compat.py`, `config/models.yaml`): the `openrouter` provider sets `enforce_zdr: true`, which adds `provider: {"zdr": true}` to every request body, restricting routing to ZDR-compliant endpoints. Redundant with the account-level policy by design, so the guarantee is reviewable in version control and survives someone relaxing the account setting. A 400/404/422 under ZDR enforcement is mapped to an explicit error naming ZDR as the likely cause, instead of a bare 404 that reads like a bad model id.
- **Per-provider configuration** (`config/models.yaml`): `base_url` / `base_url_env`, `api_key`, custom `headers`, and granular `timeouts.connect_s` / `read_s` / `write_s` per provider.
- **`limits.judge_max_context_tokens`**: above this budget a document **skips quality grading** rather than failing ingest. It is still chunked, embedded and searchable, just not graded or refined; the skip is logged and recorded on the judge result as `skipped-oversize`.

### Changed
- **BREAKING (privacy control removed)**: the documented "PrivacyGuard blocks sensitive documents from cloud/frontier APIs" control **never actually ran** — the only implementation lived in `src/atlas/concurrency.py`, which nothing imported. It has been removed by decision rather than repaired. **Privacy now rests entirely on OpenRouter zero-data-retention enforcement.** `is_sensitive` survives as a document flag (Qdrant payloads, HITL priority scoring, exports) but **does not gate provider routing**. Operators who relied on the documented behaviour should treat every ingested document as reaching the configured provider, and use the `local` profile for material that must not leave the network.
- **Read timeouts are no longer retried** (`src/atlas/llm/openai_compat.py`): a read timeout means the model accepted the request and was still generating, so replaying it identically burns another full timeout window — with `retry.llm.max_retries: 3` that is 4× the wall clock before failing anyway. Connect failures, 429s and 5xx are still retried. Tune `timeouts.read_s` per provider.
- **`fits_in_context()` checks the output ceiling too** (`src/atlas/pipeline/tokens.py`): the refine model's `roles.refine_model.max_output_tokens` is now checked alongside `limits.max_context_tokens`; exceeding either takes the sectional path. Refine emits a full rewrite, so the response is about as long as the input, and the output cap usually binds first (a model with a 1M context may cap responses at 48k). Checking only the context window was the trap this closes: the request fits, the response silently truncates, and the preservation guard then rejects the result as dropped sections — which reads like a model quality problem rather than a misconfiguration.

### Fixed
- **A busy ingest no longer freezes the whole API** (`src/atlas/pipeline/parsers.py`): Docling conversion, layout-parser ONNX inference, model downloads, and VLM page rendering ran synchronously inside async endpoints, blocking uvicorn's event loop — during a PDF parse even `GET /health` timed out, so readiness probes and the SPA stalled behind every ingest. All parser-side heavy work now runs via `asyncio.to_thread`.
- **Parse models are baked into the Docker image** (`Dockerfile`): Docling's layout (heron) + table models and deepdoc's ONNX set were fetched lazily during the *first* PDF ingest — minutes of request latency on a fresh deployment, and a hard failure wherever egress to the HF CDN is restricted. The embeddings sidecar had the same class of failure for a different reason: the TEI `cpu-1.5` image's bundled hf-hub cannot follow the relative redirect URLs the HF CDN now returns, so its weights download always failed and the container crash-looped — `docker-compose.yml` now pins `cpu-1.9`.
- **Unknown mime types are no longer parsed as PDF** (`src/atlas/pipeline/parsers.py`): with no filename, the Docling temp-file suffix defaulted to `.pdf` for any unmapped mime type; the bytes are now sniffed for `%PDF-` and anything else gets a neutral suffix so format detection fails cleanly.
- **`refine_max_section_tokens` and `refine_min_section_ratio` were never passed to `RefineNode`** (`src/atlas/pipeline/runner.py`): both keys were declared in `pipeline.yaml` and documented, but the runner never wired them through, so edits to either had no effect. Both are now passed (defaults 6000 and 0.8).
- **`refine_min_preservation_ratio` code default corrected `0.6` → `0.85`** (`src/atlas/pipeline/runner.py`), matching `config/pipeline.yaml` and the documentation. Deployments relying on the code default were running a looser guard than the one described.
- **Reasoning models returning `content: null`** (`src/atlas/llm/openai_compat.py`): when a reasoning model exhausts its token budget on thinking it can return `content: null` with a populated `reasoning` field. This previously surfaced as a `NoneType` regex failure inside tag stripping. It now raises an actionable error naming the exhausted budget and pointing at `max_tokens` for that role.

### Removed
Dead code — none of the following was reachable from a running pipeline:
- **`src/atlas/concurrency.py`** — `ConcurrencyGuard`, `ResourceGuard`, and the only `PrivacyGuard` implementation (see the BREAKING note above).
- **`src/atlas/hitl.py`** — superseded by `hitl_ledger.py`.
- **`schemas.HITLTask`, `schemas.RAGManifest`, `schemas.ChunkMetadata`**.
- **`db.session_scope`**, **`docling_adapter.parse_pdf_path`**, **`RefineNode.determine_fidelity_flag`**, **`DiagnosticsManager.get_summary`**.
- **`ingest/types.LayoutBox`, `ingest/types.OCRBox`**.
- **Config blocks** `frontier_fallback`, `cache`, `privacy`, and `judge_borderline_*`.
- **Settings** `atlas_redis_url`, `atlas_layout_table_extraction`.

## [0.7.3-dev] - unreleased

### Added
- **Swappable PDF parser backends** (`src/atlas/pipeline/ingest.py`): `pdf_parser.backend` config key now supports four modes: `auto` (Docling first → layout fallback), `auto_layout` (layout first → Docling fallback), `layout` (layout only), `docling` (Docling only). Default changed from layout-first to **Docling-first** (`auto`).
- **LLM artifact stripping** (`src/atlas/pipeline/refine.py`): `strip_llm_artifacts()` removes leaked `<think>` blocks, markdown fences, preamble/postamble boilerplate as a post-refine step.
- **Document Editor (Phase 12A+12B)**: Zero-build-step standalone HTML/JS editor, originally served by FastAPI at `/editor`. Superseded by the React SPA in 0.8.0; the editor now lives at `/app/doc/:docId` and `/app/run/:runId`, and the `/editor` mount no longer exists.
  - **Backend**: `api_editor.py` with 5 endpoints: `page-info`, `render-page`, `source-pdf`, `markdown`, `vision-refine`.
  - **Frontend**: PDF.js viewer (left) + CodeMirror 6 markdown editor (right), Split.js split view, dark theme, tool palette (VLM Fix, Strip Artifacts, Save, Undo), toast notifications.
  - **VLM support**: `page_renderer.py` — PyMuPDF-based PDF→PNG rendering with configurable DPI and header/footer crop margins. `build_vision_messages()` constructs multimodal prompts for vision refinement.
  - **`vision_model` role** in `models.yaml` — dedicated VLM configuration for page-level PDF correction.
- **React SPA Document Editor (Phase 12C)**: Replaces the standalone HTML/JS editor with a full React application.
  - **Stack**: Vite 6 + React 18 + TypeScript + shadcn/ui (Radix + CVA) + Tailwind CSS + TanStack React Query 5 + Zustand 4.
  - **30 source files** in `web/`: 10 shadcn/ui primitives, 5 editor components (PDF viewer, markdown editor, toolbar, VLM settings, status bar), typed API client, Zustand store, React Query hooks.
  - **Build**: `npm run build` → `static/app/` (served by FastAPI at `/app`). Dockerfile multi-stage: Node.js build + Python runtime.
  - **9 API endpoints** (`api_editor.py`): `resolve-doc`, `page-info`, `source-pdf`, `markdown`, `page-markdown`, `vision-refine`, `save-markdown`, `llm-refine`, `re-judge`.
  - **Developer guide**: `web/README.md` with stack reference, directory structure, design tokens, dev/build workflow.
- **VLM-first parser backend (Phase 12E)**: Full VLM ingestion workflow — interactive wizard + headless reuse.
  - **`backend: vision`** in `pdf_parser.backend`: renders all pages → VLM extracts markdown per page → deterministic stitch.
  - **`vlm_ingest` package** (`src/atlas/vlm_ingest/`): `stitcher.py` (page comment insertion, duplicate header/footer removal, table continuation merge, heading dedup), `session.py` (in-memory session registry with TTL, per-page config overrides, serializable config for headless reuse).
  - **14-endpoint API router** (`api_vlm_ingest.py` at `/api/editor/vlm-ingest`): start session (run ID or upload), configure globals + per-page overrides, thumbnails, preview, process page one-at-a-time, stitch, commit, export/import config.
  - **React wizard page** (now `/app/ingest`; `/app/vlm-ingest` redirects to it): 7-step interactive workflow (start → configure → pages → process → review → stitch → commit) with auto-advance, per-page corrections, config export.
  - **Headless mode**: `IngestNode._vlm_parse()` processes pages sequentially with no cross-page context, stitches deterministically, configurable via `pipeline.yaml → pdf_parser.vlm`.
  - **55+ new tests**: stitcher (22), session (25), API-level (8) — all passing.
- **VLM wizard PDF preview** (`ConfigureStep`): Live PDF page preview with crop guide overlays (red dashed lines), page navigation, zoom controls, and three fit modes (Fit Page, Fit Width, Actual Size) with ResizeObserver-based auto-fit.
- **Session-expired recovery banner**: Red recovery UI appears when backend session is lost (404). Zustand `sessionExpired`/`sessionExpiredReason` state + `isSessionNotFoundError()` detection helper.
- **VLM backend diagnostics**: `[VLM_DIAG]` prefix logging via `uvicorn.error` logger for session lifecycle tracing.
- **Web style guide** (`web/STYLE_GUIDE.md`): Comprehensive React page style guide covering layout patterns, preview fit modes, state wiring, error/recovery UX.
- **Multimodal ChatMessage** (`src/atlas/llm/provider.py`): `ChatMessage.content` now accepts `str | list[ContentPart]` for vision model requests (image + text blocks).
- **Unclosed `<think>` tag handling** (`src/atlas/llm/openai_compat.py`): New `_THINK_TAG_UNCLOSED_RE` regex strips truncated reasoning blocks (Qwen3 `max_tokens` exhaustion). `finish_reason` logged when not "stop" (truncation warning).
- **Unified Ingest page** (`web/src/pages/ingest/ingest-page.tsx`): Replaces separate Upload and VLM Ingest pages with a single wizard-style interface. Four methods (Docling, VLM, Import, Paste) with method-aware step progression. Old `/upload` and `/vlm-ingest` routes redirect to `/ingest`.
- **Bulk VLM processing** (`api_vlm_ingest.py`): New `POST /{session_id}/process-all` endpoint processes all pending enabled pages sequentially on the server in a single request, then auto-stitches. `ProcessAllResponse` includes per-page error tracking and the stitched result. Frontend offers "Bulk (Server)" and "Page-by-Page" mode toggle.
- **Export INDEX.md inventory** (`export_package.py`, `corpus_package.py`): All multi-doc ZIP exports (corpus, project, tenant — both full and lean) now include an `INDEX.md` markdown table listing every document with doc ID, version, workspace, project, collection, chunk count, and filename.
- **Lean export YAML frontmatter** (`export_package.py`, `corpus_package.py`): Single-doc and corpus lean markdown exports now include YAML frontmatter (`---` delimited) with scope metadata (`tenant_id`, `project_id`, `corpus_id`, `doc_id`, `doc_version`, `exported_at`). `build_frontmatter()` helper accepts arbitrary keys and skips `None` values for extensibility.
- **VLM heading formatting rules** (`page_renderer.py`): Default VLM system prompt now includes explicit heading hierarchy guidance — numbered sections (`# 1`, `## 1.1`, `### 1.1.1`) and appendix sections (`# A`, `## A.1`, `### A.1.1`).

### Changed
- **Cleanup rules optimized**: Added the `fix_numbered_headings`, `strip_headers_footers` and `merge_hardwrapped_paragraphs` step handlers, bringing `_STEP_REGISTRY` to **8** handlers, and organized the reference rule set into logical sections (heading normalization, content stripping, bullet/list cleanup, paragraph repair). This did **not** change the shipped defaults: stock `config/pipeline.yaml` ships `cleanup_rules: []` and always has — the worked ~10-rule reference set lives in `personal_configs/`, not in the stock config.
- **Builtin cleanup expansion**: `strip_page_numbers` (ON by default) and `strip_repetitive_lines` (OFF by default) now documented in `PIPELINE_REFERENCE.md`.
- **Refine guardrails tightened**: `min_preservation_ratio` raised from 0.6 → 0.85. Sectional refinement uses section-count guard and dynamic `max_tokens` scaled to input length.
- **Pipeline config wiring** (`src/atlas/pipeline/runner.py`): `pipeline_cfg.get("pdf_parser")` passed to IngestNode constructor (was hardcoded env var).
- **Test fixes for Docling-first `auto` backend**: Updated `test_layout_ingest_wiring.py` (renamed `test_backend_auto_prefers_layout` → `test_backend_auto_prefers_docling`, added `test_backend_auto_layout_prefers_layout`) and `test_docling_ingest.py` (force `backend=docling` for Docling-specific error tests to prevent auto-fallback masking).
- **Dev stack default to stable mode** (`docker-compose.dev.yml`): Removed `--reload` from uvicorn command. Prevents session loss when backend files change. Add `--reload` manually when needed.
- **VLM ingest commit for uploads** (`api_vlm_ingest.py`): `commit_session` now auto-creates a workflow run when `run_id` is `None` (PDF uploaded directly), saves source PDF as artifact, assigns `run_id` to session. Frontend commit button no longer disabled for uploads.

### Fixed
- **VLM wizard blank state after API restart**: Session data lost when uvicorn auto-reloaded. Fixed by switching dev stack to stable mode (no `--reload`).
- **VLM wizard Pages step render loop**: `useEffect` depended on entire Zustand store object, causing infinite re-renders. Fixed by selecting only stable action references.
- **VLM processing page loop**: Stale closure in `processNext` re-processed already-done pages. Fixed by reading fresh state via `useVlmIngestStore.getState()` and marking pages as `'processing'` in `onMutate`.
- **VLM page correction flicker** (`useUpdatePageResult`): Hook-level `onSuccess` set markdown to empty string before component could set the correct value. Fixed by using `variables.markdown` (the submitted input) directly.
- **VLM commit missing runId update** (`useCommit`): Backend auto-creates a workflow run for uploaded PDFs and returns `run_id`, but hook never updated the store. Fixed by writing `data.run_id` back to Zustand state on success.
- **VLM session polling after 404** (`useVlmSession`): `refetchInterval: 5_000` continued polling after backend returned 404. Fixed with conditional interval: `(query) => query.state.error ? false : 5_000`.

### Removed
- **`HLD.md`** — superseded by `ARCHITECTURE.md` + `TECHNICAL_DESIGN.md`. Git history preserves final snapshot.
- **`PDF_OVERHAUL_PLAN.md`** — completed work absorbed into `TECHNICAL_DESIGN.md` Phases 10-11.
- **`VALIDATION_REPORT.md`** — frozen v0.7.0 snapshot; test coverage tracked in CHANGELOG.
- **`CAPABILITIES_AUDIT.md`** — extreme maintenance burden (line-number references stale within days). Capability status now tracked in `TECHNICAL_DESIGN.md` roadmap and CHANGELOG.
- **Dead page files** — `web/src/pages/upload/` and `web/src/pages/vlm-ingest/` removed (superseded by unified `ingest-page.tsx`).

### Documentation
- **E2E_TEST_GUIDE.md**: Test coverage matrix expanded from 7 test files to the full set with mode annotations (now 54 files / 698 tests).
- **PIPELINE_REFERENCE.md**: Fixed `normalize` section (formatting-only since v0.6.0, not noise-stripping). Added `strip_page_numbers` and `strip_repetitive_lines` to `builtin_cleanup` table (was 3 entries, now 5).
- **ARCHITECTURE.md**: Updated parser backend description, refine section, test count (585+), HITL section, Next Steps. Added VLM ingest wizard and session-expired recovery details.
- **TECHNICAL_DESIGN.md**: Added Phases 10-12, removed deleted doc references, updated §9 (documentation), §10 (capabilities audit removed). Phase 12E completed with all sub-items checked.
- **README.md**: Removed references to deleted docs, added `pdf_parser:` to config sections, fixed `refine_min_preservation_ratio` default.
- **web/STYLE_GUIDE.md**: New React page style guide covering layout, preview fit modes, state wiring, and error/recovery UX patterns.

## [0.8.0] - 2026-03-04

Reconstructed from the tagged commit (`5cc60df`, tag `v0.8.0`) — this release
shipped without a changelog entry. The `0.7.3-dev` section above continues past
this tag: some of its items landed before it and some after, and `pyproject.toml`
was never bumped past `0.7.2`.

### Added
- **React SPA operator console**: 9 pages (Dashboard, Upload, Library, Search, Review, Editor, VLM Ingest, Admin sub-pages) built from 24 shadcn/ui components, 4 layout primitives, 6 API service modules, 4 Zustand stores and 5 React Query hooks.

### Changed
- **SPA mount moved to `/app`** (router `basename` changed from `/editor`), with build output at `static/app/` (was `static/editor/`). `Dockerfile`, `README.md`, `TECHNICAL_DESIGN.md`, `ARCHITECTURE.md`, `web/README.md`, `OPTEST.md`, `E2E_TEST_GUIDE.md` and `.gitignore` updated to match.
- Fixed API response shape mismatches, error boundaries, and dark-mode form fields.

### Removed
- **Streamlit operator console**: the `ui/` package, `.streamlit/`, `Dockerfile.ui`, `build.log`, the `ui` service in every compose file, and the `streamlit` dependency (`pyproject.toml` + `uv.lock`, −14 packages).
- **Legacy `/editor` SPA mount** in `api.py` (dead code once the SPA moved to `/app`), and the stale `static/editor/` build output.

Test count at the tag: **588 passed**.

## [0.7.2] - 2026-03-03

### Added
- **Layout-aware PDF parser** (`src/atlas/ingest/`): Complete ONNX-based PDF parsing pipeline derived from RAGFlow's deepdoc engine (Apache 2.0). Eight new modules:
  - `types.py` — `LayoutType` enum (10 types), `PDFParseResult`, `ParsedRegion`, `TableResult` dataclasses
  - `model_manager.py` — Thread-safe singleton for ONNX model download/caching from HuggingFace `InfiniFlow/deepdoc`
  - `layout_recognizer.py` — Page layout detection (auto-detects PaddleDetection/YOLOv10), NMS, noise filtering
  - `postprocess.py` — DBPostProcess (text detection) + CTCLabelDecode (text recognition)
  - `ocr.py` — TextDetector (DBNet) + TextRecognizer (CRNN batch=16) + OCR facade
  - `table_recognizer.py` — Table structure recognition, HTML output, caption detection
  - `text_extractor.py` — Hybrid pdfplumber+OCR text extraction with multi-column detection
  - `pdf_parser.py` — `LayoutPdfParser` 7-step pipeline producing structured markdown with confidence metrics
- **`ParseProfile.PDF_LAYOUT`** (`src/atlas/schemas.py`): New parse profile for layout-aware PDF parsing
- **Three new `ErrorCode` values** (`src/atlas/diagnostics.py`): `DOC_LAYOUT_MODEL_UNAVAILABLE`, `DOC_TABLE_EXTRACTION_FAILED`, `DOC_OCR_CONFIDENCE_LOW`
- **Layout parser settings** (`src/atlas/settings.py`): `atlas_pdf_parser_backend` (auto/layout/docling), `atlas_models_dir`, `atlas_layout_ocr_confidence_min`, `atlas_layout_table_extraction`, `atlas_layout_pdf_zoom`
- **PDF parser pipeline config** (`config/pipeline.yaml`): New `pdf_parser:` section with backend, zoom, ocr_confidence_min, table_extraction settings
- **Extraction metadata in routing** (`src/atlas/pipeline/routing.py`): Routes can now consider `extraction_meta.mean_ocr_confidence` for layout-parser-aware fail-fast decisions
- **79 new tests** across 6 test files: `test_layout_types.py`, `test_model_manager.py`, `test_postprocess.py`, `test_layout_ingest_wiring.py`, `test_routing_layout.py`, `test_cleanup_layout.py`

### Changed
- **Ingest backend selection** (`src/atlas/pipeline/ingest.py`): `process_doc_bytes()` now selects between layout parser and Docling based on `atlas_pdf_parser_backend` setting. Default `auto` tries layout parser first, falls back to Docling on failure or low OCR confidence. Extracted quality gates into shared `_apply_pdf_quality_gates()` method.
- **Cleanup node** (`src/atlas/pipeline/cleanup.py`): Skips `strip_page_numbers` for `PDF_LAYOUT` parse profile (layout parser already handles page noise filtering)
- **Orchestrator** (`src/atlas/pipeline/orchestrator.py`): Passes `parse_profile` in doc_context to cleanup for profile-aware transform selection
- **Runner** (`src/atlas/pipeline/runner.py`): Sets `ctx.state.parse_profile` from ingest result; surfaces extraction metadata into `ctx.results["extraction_meta"]` for routing
- **Dependencies** (`pyproject.toml`): Added `pdfplumber>=0.10`, `pyclipper>=1.3`, `shapely>=2.0`, `scikit-learn>=1.4`, `opencv-python-headless>=4.9`, `huggingface-hub>=0.20`
- Test count: **445 passed** (was 358 → +87 from 79 new + 8 regression-caught in existing suite).

## [0.7.1] - 2026-03-02

### Added
- **Token estimation utilities** (`src/atlas/pipeline/tokens.py`): New module with `estimate_tokens()` (chars/3.7 heuristic), `count_headings()` (ATX heading counter), `fits_in_context()` (pre-flight feasibility check), and `split_into_sections()` (heading-aware section splitter with secondary `###` split for oversized sections). Used by refine, routing, and orchestrator.
- **Sectional refinement** (`src/atlas/pipeline/refine.py`): New `refine_document_sectional()` method for documents exceeding the refine model's context budget. Splits via `split_into_sections()`, refines each section independently, reassembles, and applies whole-document guardrails (length + heading preservation).
- **Section-count preservation guard** (`src/atlas/pipeline/refine.py`): After the existing length guard, a new heading-count check rejects refine outputs that drop ≥20% of input headings (minimum 3 headings to trigger). Prevents the model from silently collapsing detailed sections into summaries.
- **Dynamic `max_tokens`** (`src/atlas/pipeline/refine.py`): Refine calls now compute `max_tokens = max(512, int(input_est * 1.15))` per invocation, preventing the model from truncating long outputs. `_call_refine_model()` accepts an optional `max_tokens` override.
- **Pre-refine context budget check** (`src/atlas/pipeline/routing.py`): Judge routing now annotates decisions with "sectional refinement required" when a document's estimated tokens exceed the context budget, enabling the orchestrator to dispatch sectional vs full refinement.
- **`markdown_len` in state snapshot** (`src/atlas/pipeline/state.py`): `get_next_node()` now passes the current markdown length in the state snapshot to the routing function.
- **Smart refine dispatch** (`src/atlas/pipeline/orchestrator.py`): `_process_refine()` now checks `fits_in_context()` before choosing between `refine_document()` (full) and `refine_document_sectional()` with diagnostic logging.

### Changed
- **Model swap: GPT-OSS 20B → Qwen3 split** (`config/models.yaml.example`): Judge/metadata models switched from `openai/gpt-oss-20b` (MoE, SWA=128) to `qwen3-14b` (dense, full attention). Refine model switched to `qwen3-8b` (dense, full attention). Eliminates sliding-window attention blindness and sparse-expert quality issues on long documents. Added `max_tokens` to judge (500) and metadata (1000) model configs.
- **Tighter preservation ratio** (`config/pipeline.yaml.example`): `refine_min_preservation_ratio` raised from `0.6` to `0.85` — refine outputs shorter than 85% of input are rejected. Prevents the model from summarizing instead of fixing.
- **New pipeline config keys** (`config/pipeline.yaml.example`): Added `refine_min_section_ratio: 0.8`, `limits.max_context_tokens: 16384`, `limits.refine_max_section_tokens: 6000`.
- Test count: **358 passed** (unchanged from v0.7.0).

## [0.7.0] - 2026-02-28

### Added
- **Rich judge feedback to refine** (`src/atlas/pipeline/refine.py`, `orchestrator.py`): Refine node now receives judge sub-scores, rationale, and iteration context ("Attempt X of Y — be thorough"). System prompt updated to reference per-dimension feedback. Orchestrator passes full judge result (sub_scores, confidence_rationale) to refine.
- **Per-dimension judge rationale** (`src/atlas/pipeline/judge.py`): Judge prompt expanded — rationale now covers each dimension scoring below 4 with specific issues and improvement guidance, replacing the previous single-sentence format.
- **Mixed-score few-shot example** (`src/atlas/pipeline/judge.py`): Added a fourth few-shot example with realistic mixed scores (faithfulness=5, formatting=2, cohesion=4, hallucination_risk=5) to teach the model that dimensions are independent.
- **Diminishing-returns detection** (`src/atlas/pipeline/routing.py`): If a refine attempt produces no score improvement (score unchanged), routing stops the loop and escalates to HITL instead of wasting retries.
- **Score regression rollback** (`src/atlas/pipeline/routing.py`, `state.py`): If a refine attempt makes the score worse, routing reverts the markdown to the pre-refine version. Routes to HITL if pre-refine score was also below cutoff; otherwise proceeds to metadata.
- **`RoutingDecision.rollback` field** (`src/atlas/pipeline/routing.py`): New `rollback: bool` flag on the frozen dataclass — replaces fragile string-matching for rollback detection.
- **Judge score history tracking** (`src/atlas/pipeline/state.py`): `PipelineContext.set_judge_result()` now maintains a `judge_score_history` list for routing decisions.
- **Pre-refine markdown preservation** (`src/atlas/pipeline/state.py`): `set_refine_result()` saves `pre_refine_markdown` in results for regression rollback.
- **Cleanup-rejudge cycle guard** (`src/atlas/pipeline/routing.py`, `state.py`): `cleanup_rejudge_count` tracked and capped at 1 to prevent infinite cleanup→judge→cleanup loops.
- **Rich HITL task context** (`src/atlas/pipeline/runner.py`): HITL tasks now store `judge_sub_scores`, `judge_rationale`, `judge_score_history`, `refine_retries`, `refine_total_attempts`, `last_refine_improvements`, and `last_refine_success` in task meta.
- **HITL resume loop guard** (`src/atlas/pipeline/runner.py`): `MAX_HITL_RESUMES=2` prevents infinite HITL→pipeline→HITL loops. Resume count tracked in `WorkflowRun.meta["hitl_resume_count"]`.
- **HITL rich context display** (`ui/app.py`): Review tab now surfaces judge sub-scores (colour-coded), rationale, score history, and refine attempt stats in a collapsible "Judge & refine context" panel.
- **Scope-change cache invalidation** (`ui/app.py`): Switching workspace, project, or collection now automatically clears stale cached data (runs, HITL tasks, library docs) to prevent cross-scope data leaks.
- **HITL resume failure feedback** (`ui/app.py`): Resume response status is now checked — user sees a clear warning if pipeline cannot be resumed (e.g., max resumes reached) instead of a false success message.

### Changed
- **Judge error fallback**: Changed from `score=1` / `needs_refinement=True` to `score=3` / `needs_refinement=False` — transient LLM failures no longer burn refine retries.
- **Failed refines don't burn retries** (`src/atlas/pipeline/state.py`): `set_refine_result()` only increments `refine_retries` on successful refinements. Hard cap circuit breaker at 2× `max_refine_retries` total attempts prevents infinite failure loops.
- **`JUDGE→CLEANUP` transition added** (`src/atlas/pipeline/state.py`): `valid_transitions` now allows JUDGE→CLEANUP for the `cleanup_rejudge` path.
- **`hallucination_risk` in cleanup-rejudge** (`src/atlas/pipeline/routing.py`): `content_ok` check for cleanup-rejudge now includes `hallucination_risk` alongside `faithfulness` and `cohesion`.
- **`max_refine_retries` config source fixed** (`src/atlas/pipeline/runner.py`): Now reads from `limits.refine_max_retries` (correct section) with backwards-compat fallback to `thresholds`.
- **Config defaults updated** (`config/pipeline.yaml`, `pipeline.yaml.example`): `cleanup_rejudge: true` (was `false`), `formatting` floor: `2` (was `0`), `cohesion` floor: `2` (was `0`), `refine_max_retries: 3` (example synced to match).
- **UI: Project dropdown** in sidebar between Workspace and Collection with cascading filters.
- **UI: Scope-filtered API calls** — `/admin/runs` and `/admin/hitl/tasks` now pass `tenant_id` and `project_id`.
- **UI: Text-mode upload** — `is_finalized` and `is_sensitive` checkboxes added (previously file-mode only).
- **UI: Groups create form** — hierarchy guidance and disabled Create button when parent entities missing.
- Test count: **358 passed** (up from 348 in v0.6.0).

## [0.6.0] - 2026-03-01

### Added
- **Refine content-safety guardrails** (`src/atlas/pipeline/refine.py`): Tightened `REFINE_SYSTEM_PROMPT` with explicit "MUST NOT summarise, condense, or omit" instruction. Added `min_preservation_ratio` (default 0.6) — post-refine length check rejects outputs shorter than 60% of the input, falling back to the original text. Bumped `refine_version` to v2. Fixed double system-prompt bug in `_build_prompt()`. Configurable via `pipeline.yaml` `refine_min_preservation_ratio`.
- **New cleanup builtins** (`src/atlas/pipeline/cleanup.py`): `strip_page_numbers` (ON by default) and `strip_repetitive_lines` (OFF by default) — two new configurable builtin extraction-artifact fixes. Total builtins now five (`html_unescape`, `fix_ligatures`, `strip_zero_width_chars`, `strip_page_numbers`, `strip_repetitive_lines`).
- **Runner consolidation** (`src/atlas/pipeline/runner.py`): Five shared helpers extracted (`_record_pipeline_node_runs`, `_record_normalize_node_run`, `_persist_markdown_artifact`, `_handle_hitl_pause`, `_commit_chunks_to_qdrant`). Both ingest paths (text + file) rewritten to use shared helpers. 37% line reduction (1572 → 996 lines). All silent `except: pass` blocks replaced with `log.warning`. Hoisted all inline imports to module top.
- **html_unescape deduplication**: `cleanup_rules._step_html_unescape` now delegates to `cleanup._builtin_html_unescape`, eliminating duplicate logic.
- 40 new tests in `test_phase_refactors.py`: refine guardrails, normalize boundary, runner consolidation, html_unescape dedup.
- **Cleanup rules import/export** (`src/atlas/api_admin.py`): `GET /admin/cleanup-rules/export` downloads active rules as a YAML file. `POST /admin/cleanup-rules/import` accepts YAML with `replace` (overwrite all) or `merge` (add/update by name) modes. Both endpoints validate rules against the schema before applying.
- **Cleanup rules import/export UI** (`ui/app.py`): Export button downloads `cleanup_rules.yaml`; Import panel accepts a `.yaml` file upload with replace/merge mode selector.
- 10 new tests in `test_cleanup_rules_import_export.py`: export empty/populated, import replace/merge/clear, validation errors, round-trip.

### Changed
- **Normalize refactored to formatting-only** (`src/atlas/rag/normalize.py`): `strip_noise_markdown` removed entirely. Normalize now performs whitespace/line-break formatting only. Page-number stripping and repetitive-line removal moved to cleanup builtins where they belong.
- **Normalize tracked as node run**: Normalize step now records a pipeline node run for auditability.
- `startup_validation.py` updated to recognize new builtin keys (`strip_page_numbers`, `strip_repetitive_lines`).
- Test count: **348 passed** (up from 265 in v0.5.0).

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
- **Config-driven cleanup rules engine** (`src/atlas/pipeline/cleanup_rules.py`): Declarative, first-match-wins rule engine for per-corpus / per-mime-type markdown cleanup. Seven step handlers: `strip_lines_matching`, `rewrite_pattern`, `strip_headers_footers`, `normalize_headings`, `merge_hardwrapped_paragraphs`, `fix_bullets`, `html_unescape`. Rules declared in `pipeline.yaml` `cleanup_rules:` section.
- **Builtin extraction-artifact cleanup** (`src/atlas/pipeline/cleanup.py`): Five configurable builtin cleanup toggles (`html_unescape`, `fix_ligatures`, `strip_zero_width_chars`, `strip_page_numbers`, `strip_repetitive_lines`) that run automatically during the Cleanup node before user-defined rules. First three default to ON, last two to OFF. Configured via `pipeline.yaml` `builtin_cleanup:` section.
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
