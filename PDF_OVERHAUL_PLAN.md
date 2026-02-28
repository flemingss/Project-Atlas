# PDF Ingestion Overhaul — Central Plan & Tracker

> **Objective:** Replace Docling's black-box PDF extraction with a layout-analysis-first
> parser derived from RAGFlow's `deepdoc/` engine (Apache 2.0). Invest upfront in
> extraction quality so Judge, Refine, and HITL are quality *linters*, not document
> *reconstructors*.
>
> **Status:** 🟡 Planning Complete — Ready for Execution  
> **Created:** 2026-02-28  
> **Last Updated:** 2026-02-28  
> **Owner:** Project Atlas  
> **Source Reference:** `infiniflow/ragflow` (read-only; Apache 2.0 licensed)

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Architecture Comparison](#2-architecture-comparison)
3. [What to Port from RAGFlow](#3-what-to-port-from-ragflow)
4. [What NOT to Port](#4-what-not-to-port)
5. [Phase Plan](#5-phase-plan)
   - [Phase 1 — Layout-Aware PDF Parser](#phase-1--layout-aware-pdf-parser)
   - [Phase 2 — Pre-Extraction Noise Filtering](#phase-2--pre-extraction-noise-filtering)
   - [Phase 3 — Structured Table Extraction](#phase-3--structured-table-extraction)
   - [Phase 4 — Confidence-Based Quality Routing](#phase-4--confidence-based-quality-routing)
   - [Phase 5 — Wiring, Config & Settings](#phase-5--wiring-config--settings)
   - [Phase 6 — Tests](#phase-6--tests)
   - [Phase 7 — Documentation Updates](#phase-7--documentation-updates)
   - [Phase 8 — UI Updates](#phase-8--ui-updates)
   - [Phase 9 — Docker & Deployment](#phase-9--docker--deployment)
   - [Phase 10 — Validation & Release](#phase-10--validation--release)
6. [Dependency & Model Inventory](#6-dependency--model-inventory)
7. [Risk Register](#7-risk-register)
8. [Subagent Annotations](#8-subagent-annotations)
9. [Checklist — Master Tracker](#9-checklist--master-tracker)

---

## 1. Problem Statement

**Today's flow:**
```
PDF bytes → Docling (black box) → flat markdown string → Cleanup (regex fixes)
→ Judge (LLM grades) → Refine (LLM rewrites) → HITL (human fixes)
```

**Problems:**
1. Docling flattens spatial structure at extraction time — headings, tables, reading
   order, columns are lost or garbled before Atlas ever sees the content.
2. Cleanup node applies post-hoc regex to fix extraction artefacts (page numbers,
   headers/footers, broken whitespace) — reactive, not preventive.
3. Judge scores low because the markdown is structurally broken, triggering expensive
   LLM Refine passes (1-3 per document).
4. Refine is asked to *reconstruct* document structure it never had — a task better
   solved at the visual/spatial level.
5. HITL catch rate is 15-25% because upstream quality is unreliable.
6. Multi-column PDFs produce scrambled reading order.
7. Tables come through as flat text, not structured markdown/HTML.
8. Scanned PDFs vs digital PDFs get no differentiated handling.

**Target flow:**
```
PDF bytes → Page Rendering → Layout Analysis (ONNX) → Hybrid Text Extract
→ Noise Filter (spatial) → Column Ordering → Table Structure → Structured Markdown
→ Cleanup (minimal) → Judge (mostly PASS) → Refine (rarely needed) → HITL (rarely needed)
```

**Expected impact (projected):**

| Metric | Before | After |
|--------|--------|-------|
| Judge PASS rate | ~40-60% | ~75-85% |
| Refine iterations per doc | 1-3 | 0-1 |
| HITL rate | ~15-25% | ~5-10% |
| LM Studio task volume | High | ~50% reduction |
| Table accuracy | Poor (flat text) | Good (structured) |
| Multi-column reading order | Scrambled | Correct |

---

## 2. Architecture Comparison

### RAGFlow's 7-Step PDF Pipeline (`RAGFlowPdfParser.__call__()`)

| Step | Method | What It Does |
|------|--------|-------------|
| 1 | `__images__()` | Opens PDF with pdfplumber, renders pages at 216 DPI (72 × 3 zoom), extracts programmatic chars via `dedupe_chars()`, runs OCR per page |
| 2 | `_layouts_rec()` | YOLO-based layout classification → 10 types: `text`, `table`, `figure`, `title`, `header`, `footer`, `reference`, `equation`, `figure_caption`, `table_caption`. Cross-page Counter dedup for repeating noise. |
| 3 | `_table_transformer_job()` | Dedicated table structure recognition with auto-rotation (tests 0°/90°/180°/270°, picks highest OCR confidence) |
| 4 | `_text_merge()` | KMeans column detection (`_assign_column()`) + horizontal merge of adjacent boxes in same layout region |
| 5 | `_concat_downward()` | Vertical merge (XGBoost 31-feature model — currently short-circuited to plain Y-sort in latest code) |
| 6 | `_filter_forpages()` | TOC / acknowledgement page removal, dotted-line page detection |
| 7 | `_extract_table_figure()` | Separates tables/figures into image crops + structured HTML, reattaches captions by spatial proximity |

### Atlas's Current Pipeline (11 nodes)

```
INGEST → CLEANUP → JUDGE → REFINE → METADATA → EMBEDDINGS → CHUNKING → COMMIT
                                                                    ↕
                                              HITL ←──────────────────
                                              FAILED ←────────────────
```

**Integration surface:** The new parser replaces the internals of `INGEST` (specifically
`IngestNode.process_doc_bytes()` → `docling_adapter.parse_document_path()`). Everything
downstream stays the same but receives higher-quality input.

---

## 3. What to Port from RAGFlow

All source files are under **Apache 2.0 license** (`infiniflow/ragflow`).

| Component | Source File(s) | Value | Complexity |
|-----------|---------------|-------|------------|
| **LayoutRecognizer** | `deepdoc/vision/layout_recognizer.py`, `deepdoc/vision/recognizer.py` | **HIGH** — classifies regions before text extraction | Medium |
| **OCR Hybrid Merge** | `deepdoc/parser/pdf_parser.py` (`__ocr()`) | **HIGH** — handles scanned + digital in single pass | High |
| **Table Structure Recognizer** | `deepdoc/vision/table_structure_recognizer.py` | **MEDIUM** — structured table output with row/col/headers/spans | Medium |
| **Noise Filtering** | `deepdoc/vision/layout_recognizer.py` (`keep_feats`, Counter dedup), `deepdoc/parser/pdf_parser.py` (`_filter_forpages()`, `__filterout_scraps()`) | **HIGH** — removes headers/footers/TOC at source | Low |
| **Column Detection** | `deepdoc/parser/pdf_parser.py` (`_assign_column()`) | **MEDIUM** — fixes multi-column reading order | Low |
| **Text Merging** | `deepdoc/parser/pdf_parser.py` (`_text_merge()`, `_naive_vertical_merge()`) | **MEDIUM** — assembles boxes into paragraphs | Low-Medium |
| **OCR Models** | `deepdoc/vision/ocr.py` (`TextDetector`, `TextRecognizer`, `OCR`) | **HIGH** (for scanned PDFs) — ONNX text detection + recognition | Medium |
| **Image Preprocessing** | `deepdoc/vision/operators.py` | **LOW** — standard CV transforms for model input | Low |

---

## 4. What NOT to Port

| Component | Reason |
|-----------|--------|
| `_concat_downward()` XGBoost path | Disabled in RAGFlow itself (early `return` before the model). Atlas's Cleanup node handles paragraph merging adequately. |
| `rag_tokenizer` | RAGFlow's Chinese/English tokenizer — deeply coupled to their NLP pipeline. Use simpler heuristics. |
| `_extract_table_figure()` image cropping | Atlas doesn't need figure image blobs — text extraction from figure regions is sufficient. |
| Multi-GPU parallelism (`PARALLEL_DEVICES`, `asyncio.Semaphore`) | Atlas runs single-GPU on LM Studio. Keep code single-threaded for now, design for future parallelism. |
| `VisionParser` / `PlainParser` | RAGFlow's vision-LLM PDF parsing — Atlas has its own LLM layer. |
| `settings.PARALLEL_DEVICES` wiring | Not applicable to Atlas's deployment model. |
| `rag/app/naive.py` chunking pipeline | RAGFlow's chunking is fundamentally different from Atlas's semantic chunking. |

---

## 5. Phase Plan

### Phase 1 — Layout-Aware PDF Parser

> **Goal:** Create a new PDF parsing module that uses ONNX layout analysis to produce
> structured, classified text regions from PDF pages.
>
> **Subagent candidate:** ✅ Yes — Phase 1a/1b/1c can each be separate subagent tasks.

#### 1a. ONNX Model Manager & Layout Recognizer

- [ ] Create `src/atlas/ingest/model_manager.py`
  - Model download from HuggingFace (`InfiniFlow/deepdoc`) on first use
  - Model caching to `ATLAS_MODELS_DIR` (new setting, default `./models/deepdoc`)
  - Manifest of required models: `layout.onnx`, `det.onnx`, `rec.onnx`, `ocr.res`, `tsr.onnx`
  - Version tracking / integrity check (file size or hash)
  - Lazy loading — models loaded on first call, not at startup
  - Thread-safe singleton pattern

- [ ] Create `src/atlas/ingest/layout_recognizer.py`
  - Port `LayoutRecognizer` class from `deepdoc/vision/layout_recognizer.py`
  - Port base `Recognizer` class utilities from `deepdoc/vision/recognizer.py`
    - `sort_Y_firstly()`, `sort_X_firstly()`, `overlapped_area()`, `layouts_cleanup()`
    - ONNX inference via `create_inputs()` → `self.sess.run()`
    - `postprocess()` for bbox extraction from model output
  - 10 layout type labels: `text`, `title`, `figure`, `figure_caption`, `table`,
    `table_caption`, `header`, `footer`, `reference`, `equation`
  - Noise filtering via `keep_feats` logic (header only if top 10%, footer only if bottom 10%)
  - Cross-page Counter dedup (text appearing in noise regions on 2+ pages)
  - Input: list of page images (PIL or numpy)
  - Output: list of `LayoutBox` dataclass per page (`x0, top, x1, bottom, type, score, page_number`)

- [ ] Create `src/atlas/ingest/types.py` (shared types)
  - `LayoutBox` dataclass: `x0`, `top`, `x1`, `bottom`, `layout_type`, `score`, `page_number`, `text` (optional)
  - `PageInfo` dataclass: `page_number`, `image`, `width`, `height`, `chars` (pdfplumber), `cum_height`
  - `ParsedRegion` dataclass: `layout_type`, `text`, `page_number`, `bbox`, `confidence`
  - `PDFParseResult` dataclass: `regions: list[ParsedRegion]`, `tables: list[str]`, `markdown: str`, `metadata: dict`

#### 1b. Hybrid Text Extractor

- [ ] Create `src/atlas/ingest/text_extractor.py`
  - Port hybrid extraction logic from `RAGFlowPdfParser.__ocr()`
  - **Strategy:** OCR detects text box locations → merge pdfplumber chars into boxes →
    prefer programmatic text → fall back to OCR recognition for textless boxes
  - pdfplumber char extraction with `dedupe_chars()` (port dedup logic)
  - OCR model wrapper using `TextDetector` and `TextRecognizer` from `deepdoc/vision/ocr.py`
  - Confidence scoring per box (OCR recognition confidence)
  - Batch recognition for efficiency (sorted by aspect ratio)
  - Rotation-aware crop (`get_rotate_crop_image()` — tests orientations for tall boxes)

- [ ] Create `src/atlas/ingest/ocr.py`
  - Port `TextDetector` class (DBNet text detection model)
  - Port `TextRecognizer` class (CRNN text recognition model)
  - Port `OCR` class as facade (detect → sort → crop → recognize)
  - ONNX inference with CPU/GPU auto-detection
  - `drop_score` threshold (0.5) for low-confidence filtering
  - Adapt image preprocessing from `deepdoc/vision/operators.py`

#### 1c. PDF Parser Assembly & Markdown Emitter

- [ ] Create `src/atlas/ingest/pdf_parser.py`
  - `LayoutPdfParser` class — main entry point
  - **Pipeline orchestration** (mirrors RAGFlow's `__call__`):
    1. Open PDF with pdfplumber, render pages to images
    2. Run layout recognition on page images
    3. Run hybrid text extraction per region
    4. Filter noise regions (headers/footers/references)
    5. Detect columns via KMeans (`_assign_column()` port)
    6. Merge text boxes horizontally (`_text_merge()` port)
    7. Order by column → reading order
    8. Emit structured markdown
  - Markdown emission rules:
    - `title` regions → `#`/`##`/`###` headings (infer level from font size or position)
    - `text` regions → body paragraphs
    - `table` regions → markdown table placeholder (or HTML in Phase 3)
    - `figure` regions → `[Figure: caption text]` placeholder
    - `figure_caption` / `table_caption` → attached to nearest figure/table
    - `equation` regions → `$$...$$` block or raw text
  - **Fallback:** If layout model fails or produces no regions, return empty result
    (caller falls back to Docling)
  - `__call__(pdf_bytes_or_path, from_page=0, to_page=100000)` → `PDFParseResult`

- [ ] Wire into `IngestNode.process_doc_bytes()` in `src/atlas/pipeline/ingest.py`
  - For `application/pdf` MIME type:
    1. Try `LayoutPdfParser` first
    2. If successful and markdown is non-empty → return result
    3. If parser fails → fall back to Docling (existing path)
  - Map `PDFParseResult` → `IngestResult`
  - Set `parse_profile` to new `ParseProfile.PDF_LAYOUT` value
  - Include extraction confidence metadata in `IngestResult.meta`

---

### Phase 2 — Pre-Extraction Noise Filtering

> **Goal:** Headers, footers, page numbers, TOC pages, and repeating noise are removed
> at the spatial level before text enters the markdown stream.
>
> **Subagent candidate:** ✅ Yes — can be done as a single focused task.

- [ ] Port noise filtering into `LayoutPdfParser`:
  - **Header/footer exclusion:** Regions with `layout_type in ["header", "footer"]` are
    excluded from markdown output (already classified by layout recognizer)
  - **Cross-page dedup:** Text appearing in noise regions on ≥2 pages is dropped
    (RAGFlow's `Counter` approach in `LayoutRecognizer.__call__()`)
  - **TOC page detection:** Port `_filter_forpages()` — detect "Contents" / "目录" pages
    and pages with 3+ dotted-line entries, remove entirely
  - **Scrap filtering:** Port `__filterout_scraps()` — drop narrow, short text fragments
    that don't form meaningful content (width < page_width/3 and height < mean_height)
  - **Reference section handling:** Regions with `layout_type == "reference"` can be
    optionally excluded (configurable)

- [ ] Update `builtin_cleanup` behaviour for layout-parsed PDFs:
  - When `parse_profile == PDF_LAYOUT`, the following builtins become no-ops
    (noise was already filtered at extraction):
    - `strip_page_numbers` — already handled by layout recognizer
    - `strip_repetitive_lines` — already handled by cross-page dedup
  - Keep all builtins active for `PDF_TEXT` (Docling) parse profile for backward compat

- [ ] Add `ParseProfile.PDF_LAYOUT` handling to `CleanupNode.clean()` in
  `src/atlas/pipeline/cleanup.py`
  - Skip redundant builtins when `parse_profile == PDF_LAYOUT`
  - Log which builtins were skipped and why

---

### Phase 3 — Structured Table Extraction

> **Goal:** Tables are extracted as proper HTML `<table>` or markdown tables instead of
> flat text.
>
> **Subagent candidate:** ✅ Yes — isolated component with clear inputs/outputs.

- [ ] Create `src/atlas/ingest/table_recognizer.py`
  - Port `TableStructureRecognizer` from `deepdoc/vision/table_structure_recognizer.py`
  - ONNX model (`tsr.onnx`) for detecting: `table`, `table column`, `table row`,
    `table column header`, `table projected row header`, `table spanning cell`
  - Row/column alignment normalization (align left/right for rows, top/bottom for columns)
  - Port `construct_table()` for HTML output:
    - Row-first sorting, column assignment, span calculation
    - Header row detection (block type analysis via `blockType()`)
    - `<table>`, `<caption>`, `<tr>`, `<th>`/`<td>`, `colspan`/`rowspan`
  - Port `is_caption()` for caption detection near table regions
  - Add markdown table output option (simpler, no spans — for non-complex tables)

- [ ] Integrate into `LayoutPdfParser`:
  - When a `table` region is detected by layout recognizer:
    1. Crop the table region from page image
    2. Run `TableStructureRecognizer` on cropped image
    3. Extract text per cell (using hybrid text extractor)
    4. Build HTML table via `construct_table()`
    5. Emit as HTML block in markdown output (fenced or raw)
  - Auto-rotation for tables (port `_table_transformer_job()` rotation logic):
    - Test 0°, 90°, 180°, 270° rotations
    - Pick rotation with highest average OCR confidence
    - Re-extract text at best rotation

- [ ] Handle cross-page tables:
  - Port merge logic from `_extract_table_figure()`: if a table region on page N has
    no caption but continues from a table on page N-1, merge them

---

### Phase 4 — Confidence-Based Quality Routing

> **Goal:** Use extraction-time confidence signals to route documents intelligently,
> avoiding wasted LLM calls on documents that need OCR retry or direct HITL.
>
> **Subagent candidate:** ❌ No — touches routing logic, best done by primary agent.

- [ ] Enhance `PDFParseResult` with confidence signals:
  - `mean_ocr_confidence`: average OCR recognition confidence across all boxes
  - `layout_detection_scores`: per-region detection confidence from layout model
  - `programmatic_text_ratio`: fraction of text from pdfplumber vs OCR
  - `low_confidence_pages`: list of page numbers with mean confidence < threshold
  - `estimated_is_scanned`: bool heuristic (< 10 pdfplumber chars per page)

- [ ] Auto-retry low-confidence pages:
  - If mean OCR confidence for a page < 0.6, retry at higher DPI (4× zoom instead of 3×)
  - Port RAGFlow's retry logic from `__images__()` (retry at `3 * zoomin` when no boxes found)
  - Cap retries at 1 per page to avoid infinite loops

- [ ] Wire confidence into `IngestResult.meta`:
  - Add `extraction_confidence` dict to meta
  - Update `docling_health.py` → `compute_health()` to incorporate layout parser signals
    (or create parallel `layout_health()` function)

- [ ] Update routing in `src/atlas/pipeline/routing.py`:
  - New pre-Judge routing rule: if `extraction_confidence.mean_ocr_confidence < 0.4`
    and `programmatic_text_ratio < 0.1`, route directly to HITL with reason
    `"OCR confidence too low for automated processing"`
  - Add `extraction_confidence` to the `state_snapshot` passed to `decide_next_step()`

- [ ] Update `PipelineContext` in `src/atlas/pipeline/state.py`:
  - Add `extraction_confidence` field to state
  - Ensure it's populated from `IngestResult.meta` in `runner.py`

---

### Phase 5 — Wiring, Config & Settings

> **Goal:** All new functionality is properly configured, gated behind feature flags,
> and wired into the existing pipeline infrastructure.
>
> **Subagent candidate:** ✅ Yes — config/settings changes are mechanical.

#### 5a. Settings (`src/atlas/settings.py`)

- [ ] Add new settings:
  ```python
  # --- PDF Layout Parser ---
  atlas_pdf_parser: str = "auto"             # "auto" | "layout" | "docling"
  atlas_models_dir: str = "./models/deepdoc" # ONNX model storage
  atlas_layout_confidence_threshold: float = 0.3  # Min layout detection score
  atlas_ocr_drop_score: float = 0.5          # Min OCR recognition confidence
  atlas_ocr_retry_dpi_multiplier: float = 4.0     # DPI multiplier for retry
  atlas_ocr_confidence_floor: float = 0.4    # Below this → HITL routing
  atlas_table_extraction_enabled: bool = True      # Enable table structure recognition
  atlas_layout_exclude_references: bool = False    # Exclude reference sections
  ```

#### 5b. Config (`config/pipeline.yaml`)

- [ ] Add `pdf_parser` section to pipeline.yaml:
  ```yaml
  pdf_parser:
    backend: auto           # auto | layout | docling
    layout_confidence: 0.3  # Min detection confidence for layout regions
    ocr_drop_score: 0.5     # Min OCR recognition confidence
    noise_filter:
      exclude_headers: true
      exclude_footers: true
      exclude_references: false
      cross_page_dedup: true
      toc_detection: true
    table_extraction:
      enabled: true
      output_format: html   # html | markdown
      auto_rotate: true
    retry:
      enabled: true
      dpi_multiplier: 4.0
      max_retries_per_page: 1
  ```

- [ ] Update `config/pipeline.yaml.example` with new section + comments
- [ ] Update `config/models.yaml.example` if any model-related config changes

#### 5c. Schemas (`src/atlas/schemas.py`)

- [ ] Add `ParseProfile.PDF_LAYOUT` to the `ParseProfile` enum
- [ ] Verify `ChunkMetadata.parse_profile` can hold the new value
- [ ] Verify `DocumentIngestState.parse_profile` accepts it

#### 5d. Diagnostics (`src/atlas/diagnostics.py`)

- [ ] Add new `ErrorCode` values:
  - `DOC_LAYOUT_MODEL_UNAVAILABLE` — layout ONNX model not found/downloadable
  - `DOC_OCR_MODEL_UNAVAILABLE` — OCR ONNX model not found/downloadable
  - `DOC_LAYOUT_PARSE_FAILED` — layout parser crashed (triggers Docling fallback)
  - `DOC_OCR_CONFIDENCE_LOW` — OCR confidence below routing threshold

#### 5e. Startup Validation (`src/atlas/startup_validation.py`)

- [ ] Add validation for `pdf_parser` config section in `_validate_config_shapes()`:
  - Valid `backend` values: `auto`, `layout`, `docling`
  - Numeric ranges for confidence thresholds
  - Boolean toggles for noise filter and table extraction
- [ ] Add optional model availability check (warn if models not downloaded, but don't
  fail startup — models download on first use)
- [ ] Validate `atlas_models_dir` is writable if `backend != "docling"`

#### 5f. Pipeline Runner Wiring (`src/atlas/pipeline/runner.py`)

- [ ] Update `ingest_file_via_pipeline()`:
  - Pass `pdf_parser` config to `IngestNode` constructor (or to `process_doc_bytes()`)
  - Populate `extraction_confidence` in `PipelineContext` from `IngestResult.meta`
- [ ] Update node-run recording to capture layout parser metadata (model version,
  regions detected, confidence scores)
- [ ] Ensure artifact persistence captures layout parse profile

#### 5g. Orchestrator (`src/atlas/pipeline/orchestrator.py`)

- [ ] Pass `parse_profile` to Cleanup node so it can skip redundant builtins
- [ ] No structural changes needed — orchestrator is node-agnostic

---

### Phase 6 — Tests

> **Goal:** Comprehensive test coverage for all new modules, integration with existing
> test suite (currently 358 tests, 100% pass rate).
>
> **Subagent candidate:** ✅ Yes — test writing is highly parallelizable. Can spawn
> one subagent per test file/module.

#### 6a. Unit Tests — New Modules

- [ ] `tests/test_layout_recognizer.py`
  - Test ONNX model loading (mocked)
  - Test `LayoutBox` classification for known input
  - Test noise filtering logic (keep_feats)
  - Test cross-page Counter dedup
  - Test `layouts_cleanup()` NMS dedup
  - Test sort utilities (sort_Y_firstly, sort_X_firstly)
  - Mock ONNX session to avoid model download in CI

- [ ] `tests/test_text_extractor.py`
  - Test hybrid extraction: pdfplumber chars merged into OCR boxes
  - Test programmatic text preference over OCR
  - Test OCR fallback for empty boxes
  - Test confidence scoring
  - Test `dedupe_chars()` port
  - Test batch recognition sorting (by aspect ratio)

- [ ] `tests/test_ocr.py`
  - Test `TextDetector` with mocked ONNX session
  - Test `TextRecognizer` with mocked ONNX session
  - Test `OCR` facade (detect → sort → crop → recognize)
  - Test `drop_score` filtering
  - Test rotation-aware crop for tall boxes

- [ ] `tests/test_table_recognizer.py`
  - Test TSR model inference (mocked)
  - Test row/column alignment normalization
  - Test `construct_table()` → HTML output
  - Test `is_caption()` detection
  - Test span calculation (`__cal_spans()`)
  - Test header row detection (blockType analysis)

- [ ] `tests/test_pdf_parser.py`
  - Test `LayoutPdfParser.__call__()` end-to-end (with mocked models)
  - Test column detection via KMeans
  - Test horizontal text merge
  - Test markdown emission for each layout type
  - Test structured output: headings, paragraphs, tables, figures
  - Test Docling fallback when layout parser returns empty result
  - Test page rendering at correct DPI

- [ ] `tests/test_model_manager.py`
  - Test model download (mocked HTTP)
  - Test model caching (skip download if exists)
  - Test lazy loading (no load until first use)
  - Test thread-safe singleton
  - Test missing model directory creation

#### 6b. Integration Tests — Modified Modules

- [ ] Update `tests/test_docling_ingest.py`
  - Add tests for layout parser path (mock `LayoutPdfParser`)
  - Test fallback from layout parser to Docling
  - Test `ParseProfile.PDF_LAYOUT` in ingest result
  - Test extraction confidence metadata propagation
  - Test `pdf_parser: layout` vs `pdf_parser: docling` vs `pdf_parser: auto` config

- [ ] Update `tests/test_docling_health.py`
  - Add tests for layout parser health signals
  - Test `extraction_confidence` in health computation

- [ ] Update `tests/test_pipeline_nodes.py`
  - Add test for Judge scoring a layout-parsed document
  - Verify `parse_profile=PDF_LAYOUT` is carried through pipeline

- [ ] Update `tests/test_cleanup.py`
  - Test that builtins are skipped for `PDF_LAYOUT` parse profile
  - Test that builtins still fire for `PDF_TEXT` parse profile

- [ ] Update `tests/test_routing.py`
  - Add test for low-OCR-confidence → HITL routing
  - Test `extraction_confidence` in routing decision

- [ ] Update `tests/test_startup_validation.py`
  - Test validation of `pdf_parser` config section
  - Test validation of model directory settings

- [ ] Update `tests/test_pipeline_state.py`
  - Test `extraction_confidence` field in state

- [ ] Update `tests/test_schemas.py`
  - Test `ParseProfile.PDF_LAYOUT` enum value

#### 6c. E2E Tests

- [ ] Update `scripts/e2e_scenarios.py`
  - Add deterministic scenario for layout-parsed PDF
  - Add scenario for Docling fallback when layout parser fails
  - Add scenario for table extraction

- [ ] Update `scripts/e2e_runner.py` if needed (model availability)

---

### Phase 7 — Documentation Updates

> **Goal:** All docs are current with the new architecture, configuration options, and
> operational procedures.
>
> **Subagent candidate:** ✅ Yes — doc updates are independent and parallelizable.

- [ ] Update `README.md`
  - Add PDF parser backend selection to configuration section
  - Update dependency list (pdfplumber, opencv-python-headless)
  - Add ONNX model download instructions
  - Update "How it works" section with layout analysis step

- [ ] Update `TECHNICAL_DESIGN.md`
  - Add new section for PDF Layout Parser architecture
  - Update §3 implementation status (new modules)
  - Update §5 end-state architecture diagram
  - Add Phase 10 (PDF Overhaul) to phased roadmap

- [ ] Update `ARCHITECTURE.md`
  - Add Layout Parser module description
  - Update Pipeline section with new ingest flow
  - Add ONNX model management section

- [ ] Update `CAPABILITIES_AUDIT.md`
  - Add layout-aware PDF parsing capability
  - Add structured table extraction capability
  - Add OCR hybrid extraction capability
  - Update capability count

- [ ] Update `CHANGELOG.md`
  - Add version entry for PDF overhaul release
  - List all new features, modules, config options

- [ ] Update `config/PIPELINE_REFERENCE.md`
  - Add `pdf_parser` config section documentation
  - Document all new settings with defaults, types, and examples
  - Add noise filter and table extraction sub-sections

- [ ] Update `E2E_TEST_GUIDE.md`
  - Add layout parser E2E scenarios
  - Update test coverage matrix

- [ ] Update `VALIDATION_REPORT.md`
  - Will need regeneration after all tests pass

---

### Phase 8 — UI Updates

> **Goal:** The Streamlit UI surfaces the new parsing backend, extraction confidence,
> and layout analysis results to operators.
>
> **Subagent candidate:** ✅ Yes — UI changes are self-contained.

- [ ] **Upload tab** (`ui/app.py`):
  - Add PDF parser backend selector (Auto / Layout / Docling) — respects config default
  - Show extraction method used in processing result (layout vs docling vs fallback)
  - Display extraction confidence summary after ingestion

- [ ] **Review tab** (`ui/app.py`):
  - Show `parse_profile` (PDF_LAYOUT vs PDF_TEXT) in HITL task detail
  - Show extraction confidence if available in task context
  - Add visual indicator for layout-parsed vs Docling-parsed documents

- [ ] **Admin tab** (`ui/app.py`):
  - Add "PDF Parser" sub-section in Health & Metrics:
    - Model download status (layout.onnx, det.onnx, rec.onnx, tsr.onnx)
    - Model file sizes and paths
    - Current parser backend setting
  - Add parser backend toggle (with config persistence)

- [ ] **My Collection tab** (`ui/app.py`):
  - Show parse profile in document detail view
  - Show extraction confidence in document metadata

- [ ] **Theme updates** (`ui/theme.py`):
  - Add colour token for layout-parse indicator (if needed)
  - Add microcopy for parser backend selector

- [ ] **Component updates** (`ui/components.py`):
  - Add `confidence_badge()` component for extraction confidence display
  - Add `parser_indicator()` component for parse profile display

- [ ] Update `ui/UI_LAYOUT_PLAN.md` with new UI elements
- [ ] Update `ui/STYLE_GUIDE.md` if new components/tokens added

---

### Phase 9 — Docker & Deployment

> **Goal:** Docker images, compose files, and deployment configs support the new
> parser and its ONNX model dependencies.
>
> **Subagent candidate:** ✅ Yes — Docker/infra changes are self-contained.

- [ ] Update `Dockerfile`:
  - Add system dependencies for OpenCV (`libgl1-mesa-glx`, `libglib2.0-0` — already present, verify)
  - Add `pdfplumber` and `opencv-python-headless` to dependencies if not already pulled by docling
  - Add model download step (optional — can also download on first use):
    ```dockerfile
    # Pre-download ONNX models for faster cold start (optional)
    # RUN python -c "from atlas.ingest.model_manager import ensure_models; ensure_models()"
    ```
  - Create `models/` directory in image

- [ ] Update `docker-compose.yml`:
  - Add `ATLAS_MODELS_DIR` environment variable
  - Add volume mount for model persistence: `./volumes/models:/app/models`
  - Add `ATLAS_PDF_PARSER` environment variable (default: `auto`)

- [ ] Update `docker-compose.dev.yml`, `docker-compose.e2e.yml`, `docker-compose.optest.yml`:
  - Mirror model volume and env var changes
  - E2E/optest may need model pre-download or mock

- [ ] Update `pyproject.toml` dependencies:
  - Add `pdfplumber>=0.10` (if not already a transitive dep of docling)
  - Add `opencv-python-headless>=4.8` (if not already available)
  - Add `scikit-learn>=1.3` (for KMeans column detection)
  - Add `xgboost>=2.0` (optional — only if we enable the concat model later)
  - Verify `onnxruntime>=1.17` is sufficient (already listed)
  - Verify `PyMuPDF>=1.24.0` is sufficient (already listed)

- [ ] Add `.dockerignore` entry for `models/` directory (don't copy host models into build context)

---

### Phase 10 — Validation & Release

> **Goal:** Full validation pass, version bump, and release.
>
> **Subagent candidate:** ❌ No — release validation must be done holistically.

- [ ] Run full test suite: `pytest` — target: all existing + new tests pass
- [ ] Run ruff checks: `ruff check` + `ruff format` — target: clean
- [ ] Run E2E scenarios (deterministic mode): all scenarios pass
- [ ] Run E2E scenarios (local LLM / LM Studio mode): key scenarios pass
- [ ] Manual smoke test: upload a multi-column PDF, verify correct reading order
- [ ] Manual smoke test: upload a table-heavy PDF, verify structured table output
- [ ] Manual smoke test: upload a scanned PDF, verify OCR extraction works
- [ ] Manual smoke test: force layout parser failure, verify Docling fallback works
- [ ] Verify Docker build succeeds and container starts
- [ ] Verify model auto-download works in container
- [ ] Update version in `pyproject.toml` (0.7.0 or 0.8.0)
- [ ] Regenerate `VALIDATION_REPORT.md` with new test counts
- [ ] Update `CAPABILITIES_AUDIT.md` with new capability wiring status
- [ ] Final `CHANGELOG.md` entry
- [ ] Git tag & commit

---

## 6. Dependency & Model Inventory

### Python Packages (New/Updated)

| Package | Purpose | Already in deps? | Required? |
|---------|---------|-------------------|-----------|
| `pdfplumber` | PDF page rendering, char extraction | Check (maybe via docling) | **Yes** |
| `opencv-python-headless` | Image preprocessing for ONNX models | Check | **Yes** |
| `scikit-learn` | KMeans for column detection | No | **Yes** |
| `numpy` | Array operations | Yes (transitive) | Yes |
| `Pillow` | Image manipulation | Yes (transitive) | Yes |
| `onnxruntime` | ONNX model inference | Yes (`>=1.17`) | Yes |
| `PyMuPDF` | PDF preflight (already used) | Yes (`>=1.24.0`) | Yes |
| `huggingface_hub` | Model download | Check | **Yes** |
| `xgboost` | Paragraph concat model (future) | No | **No** (Phase 5+) |

### ONNX Models (from `InfiniFlow/deepdoc` on HuggingFace)

| Model | File | Size (approx) | Purpose | Required Phase |
|-------|------|---------------|---------|---------------|
| Layout Recognizer | `layout.onnx` | ~50 MB | 10-class region classification | Phase 1a |
| Text Detector | `det.onnx` | ~5 MB | DBNet text box detection | Phase 1b |
| Text Recognizer | `rec.onnx` | ~10 MB | CRNN text recognition | Phase 1b |
| OCR Dictionary | `ocr.res` | ~500 KB | Character dictionary for recognizer | Phase 1b |
| Table Structure | `tsr.onnx` | ~50 MB | Table row/column/header detection | Phase 3 |
| Concat Model | `updown_concat_xgb.model` | ~1 MB | Paragraph boundary (future) | Not planned |

**Total model footprint:** ~115 MB (first download), cached locally thereafter.

---

## 7. Risk Register

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| R1 | ONNX models don't work on Windows (Atlas dev environment) | Blocks development | Low | Test early; ONNX Runtime supports Windows; CPU fallback available |
| R2 | Layout model accuracy is poor on Atlas's target PDFs | Wasted effort | Medium | Test with real corpus PDFs in Phase 1 before building Phases 2-4; keep Docling fallback |
| R3 | Model download fails in air-gapped environments | Blocks deployment | Medium | Support manual model placement in `ATLAS_MODELS_DIR`; document offline setup |
| R4 | OpenCV headless has system dependency conflicts in Docker | Blocks Docker build | Low | Already have libgl1 in Dockerfile; test early |
| R5 | pdfplumber conflicts with Docling's internal PDF handling | Runtime errors | Low | They use different PDF backends; test coexistence |
| R6 | Test count inflates significantly, slowing CI | Developer friction | Medium | Keep new tests fast (mock ONNX, no real model inference in unit tests) |
| R7 | XGBoost concat model is needed but adds heavy dependency | Scope creep | Low | Don't port it — use simpler heuristic merge (RAGFlow disabled it too) |
| R8 | Table HTML output confuses downstream chunking | Chunking errors | Medium | Test chunking with HTML tables; may need chunking strategy update to handle `<table>` blocks |
| R9 | Layout parser is slower than Docling for simple digital PDFs | Regression | Medium | `auto` mode: detect digital PDFs (high pdfplumber char count) and skip OCR; benchmark |
| R10 | Breaking change to `IngestResult` / `ParseProfile` / API contracts | Integration failures | Low | Additive changes only; new enum value, new optional meta fields |

---

## 8. Subagent Annotations

Subagents can be spawned for parallelizable, self-contained tasks. Each annotation
identifies the phase, task scope, inputs needed, and expected output.

| ID | Phase | Task | Inputs | Output | Dependencies |
|----|-------|------|--------|--------|-------------|
| **SA-1** | 1a | Port LayoutRecognizer + Recognizer base | RAGFlow source: `layout_recognizer.py`, `recognizer.py`, `operators.py` | `layout_recognizer.py`, `types.py`, unit tests | None |
| **SA-2** | 1b | Port OCR pipeline (TextDetector, TextRecognizer, hybrid merge) | RAGFlow source: `ocr.py`, `pdf_parser.py` (`__ocr()`) | `ocr.py`, `text_extractor.py`, unit tests | SA-1 (for types) |
| **SA-3** | 1a | Create model_manager.py | HuggingFace `InfiniFlow/deepdoc` repo structure | `model_manager.py`, unit tests | None |
| **SA-4** | 1c | Build LayoutPdfParser + markdown emitter | SA-1 + SA-2 outputs | `pdf_parser.py`, unit tests | SA-1, SA-2 |
| **SA-5** | 3 | Port TableStructureRecognizer | RAGFlow source: `table_structure_recognizer.py` | `table_recognizer.py`, unit tests | SA-1 (for base Recognizer) |
| **SA-6** | 5 | Config, settings, schemas, diagnostics, validation updates | Current Atlas config files | Updated config files, unit tests | SA-4 (to know final API shape) |
| **SA-7** | 6 | Integration test updates | All new modules | Updated test files | SA-4, SA-5, SA-6 |
| **SA-8** | 7 | Documentation updates | All completed phases | Updated .md files | SA-4, SA-5, SA-6, SA-7 |
| **SA-9** | 8 | UI updates | All completed phases | Updated ui/ files | SA-4, SA-6 |
| **SA-10** | 9 | Docker & deployment updates | Final dependency list | Updated Docker files | SA-6 |

**Recommended execution order:**
```
SA-1 + SA-3 (parallel)      →  SA-2  →  SA-4 + SA-5 (parallel)
                                          ↓
                              SA-6  →  SA-7 + SA-8 + SA-9 + SA-10 (parallel)
```

**Critical path:** SA-1 → SA-2 → SA-4 → SA-6 → SA-7

---

## 9. Checklist — Master Tracker

### Phase 1 — Layout-Aware PDF Parser
- [ ] **1a-01** `src/atlas/ingest/model_manager.py` — model download & caching
- [ ] **1a-02** `src/atlas/ingest/types.py` — shared dataclasses
- [ ] **1a-03** `src/atlas/ingest/layout_recognizer.py` — ONNX layout classification
- [ ] **1b-01** `src/atlas/ingest/ocr.py` — TextDetector + TextRecognizer
- [ ] **1b-02** `src/atlas/ingest/text_extractor.py` — hybrid text extraction
- [ ] **1c-01** `src/atlas/ingest/pdf_parser.py` — LayoutPdfParser assembly
- [ ] **1c-02** Wire into `IngestNode.process_doc_bytes()` with Docling fallback
- [ ] **1c-03** Map `PDFParseResult` → `IngestResult`

### Phase 2 — Noise Filtering
- [ ] **2-01** Header/footer exclusion in LayoutPdfParser
- [ ] **2-02** Cross-page Counter dedup
- [ ] **2-03** TOC page detection + removal
- [ ] **2-04** Scrap filtering
- [ ] **2-05** Update CleanupNode for PDF_LAYOUT parse profile

### Phase 3 — Table Extraction
- [ ] **3-01** `src/atlas/ingest/table_recognizer.py` — TSR ONNX model
- [ ] **3-02** Table HTML construction (`construct_table()`)
- [ ] **3-03** Caption detection + reattachment
- [ ] **3-04** Auto-rotation for tables
- [ ] **3-05** Cross-page table merging
- [ ] **3-06** Wire into LayoutPdfParser

### Phase 4 — Quality Routing
- [ ] **4-01** Confidence signals in PDFParseResult
- [ ] **4-02** Auto-retry low-confidence pages
- [ ] **4-03** Wire confidence into IngestResult.meta
- [ ] **4-04** Update routing.py with OCR confidence check
- [ ] **4-05** Update PipelineContext with extraction_confidence

### Phase 5 — Wiring & Config
- [ ] **5a-01** New settings in `settings.py`
- [ ] **5b-01** New `pdf_parser` section in `pipeline.yaml`
- [ ] **5b-02** Update `pipeline.yaml.example`
- [ ] **5c-01** Add `ParseProfile.PDF_LAYOUT` to schemas
- [ ] **5d-01** New ErrorCode values in diagnostics
- [ ] **5e-01** Startup validation for pdf_parser config
- [ ] **5e-02** Optional model availability warning
- [ ] **5f-01** Update runner.py wiring
- [ ] **5g-01** Pass parse_profile to CleanupNode

### Phase 6 — Tests
- [ ] **6a-01** `tests/test_layout_recognizer.py`
- [ ] **6a-02** `tests/test_text_extractor.py`
- [ ] **6a-03** `tests/test_ocr.py`
- [ ] **6a-04** `tests/test_table_recognizer.py`
- [ ] **6a-05** `tests/test_pdf_parser.py` (new parser)
- [ ] **6a-06** `tests/test_model_manager.py`
- [ ] **6b-01** Update `tests/test_docling_ingest.py`
- [ ] **6b-02** Update `tests/test_docling_health.py`
- [ ] **6b-03** Update `tests/test_pipeline_nodes.py`
- [ ] **6b-04** Update `tests/test_cleanup.py`
- [ ] **6b-05** Update `tests/test_routing.py`
- [ ] **6b-06** Update `tests/test_startup_validation.py`
- [ ] **6b-07** Update `tests/test_pipeline_state.py`
- [ ] **6b-08** Update `tests/test_schemas.py`
- [ ] **6c-01** E2E scenario: layout-parsed PDF
- [ ] **6c-02** E2E scenario: Docling fallback
- [ ] **6c-03** E2E scenario: table extraction

### Phase 7 — Documentation
- [ ] **7-01** Update `README.md`
- [ ] **7-02** Update `TECHNICAL_DESIGN.md`
- [ ] **7-03** Update `ARCHITECTURE.md`
- [ ] **7-04** Update `CAPABILITIES_AUDIT.md`
- [ ] **7-05** Update `CHANGELOG.md`
- [ ] **7-06** Update `config/PIPELINE_REFERENCE.md`
- [ ] **7-07** Update `E2E_TEST_GUIDE.md`
- [ ] **7-08** Regenerate `VALIDATION_REPORT.md`

### Phase 8 — UI Updates
- [ ] **8-01** Upload tab: parser backend selector + results display
- [ ] **8-02** Review tab: parse profile + confidence in HITL detail
- [ ] **8-03** Admin tab: PDF Parser health section
- [ ] **8-04** My Collection tab: parse profile + confidence display
- [ ] **8-05** Theme + components: new tokens and widgets
- [ ] **8-06** Update `ui/UI_LAYOUT_PLAN.md`
- [ ] **8-07** Update `ui/STYLE_GUIDE.md`

### Phase 9 — Docker & Deployment
- [ ] **9-01** Update `Dockerfile`
- [ ] **9-02** Update `docker-compose.yml` (env vars, volumes)
- [ ] **9-03** Update dev/e2e/optest compose files
- [ ] **9-04** Update `pyproject.toml` dependencies
- [ ] **9-05** Add `.dockerignore` entry for models/

### Phase 10 — Validation & Release
- [ ] **10-01** Full pytest suite passes
- [ ] **10-02** Ruff clean
- [ ] **10-03** E2E deterministic scenarios pass
- [ ] **10-04** E2E local LLM scenarios pass
- [ ] **10-05** Manual smoke: multi-column PDF
- [ ] **10-06** Manual smoke: table-heavy PDF
- [ ] **10-07** Manual smoke: scanned PDF
- [ ] **10-08** Manual smoke: Docling fallback
- [ ] **10-09** Docker build + container start
- [ ] **10-10** Model auto-download in container
- [ ] **10-11** Version bump in pyproject.toml
- [ ] **10-12** Regenerate VALIDATION_REPORT.md
- [ ] **10-13** Final CAPABILITIES_AUDIT.md update
- [ ] **10-14** Final CHANGELOG.md entry
- [ ] **10-15** Git tag & commit

---

> **Total items:** 76 tracked tasks across 10 phases.
>
> **Critical path phases:** 1 → 5 → 6 → 10  
> **Parallelizable phases:** 2+3 (after 1), 7+8+9 (after 5/6)
>
> **Estimated effort:** Phase 1 is the heaviest (~60% of code). Phases 2-4 build
> incrementally on Phase 1. Phases 5-9 are mechanical wiring. Phase 10 is validation.
