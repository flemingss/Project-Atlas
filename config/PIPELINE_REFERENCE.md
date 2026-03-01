# Pipeline Configuration Reference

> **Applies to:** `config/pipeline.yaml` (or the active DB config version)
> **Schema version:** `1`

---

## Table of Contents

- [Overview](#overview)
- [Config Layering](#config-layering)
- [Top-Level Keys](#top-level-keys)
  - [`version`](#version)
  - [`thresholds`](#thresholds)
  - [`limits`](#limits)
  - [`normalize`](#normalize)
  - [`chunking`](#chunking)
  - [`frontier_fallback`](#frontier_fallback)
  - [`cache`](#cache)
  - [`privacy`](#privacy)
  - [`retry`](#retry)
  - [`builtin_cleanup`](#builtin_cleanup)
  - [`cleanup_rules`](#cleanup_rules)
  - [`pdf_parser`](#pdf_parser)
- [Cleanup Rules — Full Reference](#cleanup-rules--full-reference)
  - [Rule Structure](#rule-structure)
  - [Match Block](#match-block)
  - [Step Kinds](#step-kinds)
  - [Tags and Routing](#tags-and-routing)
  - [Rule Matching Behaviour](#rule-matching-behaviour)
  - [Examples](#examples)
- [Pipeline Flow](#pipeline-flow)
  - [Node Sequence](#node-sequence)
  - [Routing Decisions](#routing-decisions)
  - [Built-in Cleanup Transforms](#built-in-cleanup-transforms)
  - [Docling Health Scoring](#docling-health-scoring)
  - [Chunk Quality Assurance](#chunk-quality-assurance)
- [Admin API Endpoints](#admin-api-endpoints)
- [Operational Workflows](#operational-workflows)
  - [Editing a Rule in the UI](#editing-a-rule-in-the-ui)
  - [Testing a Rule (Dry-Run)](#testing-a-rule-dry-run)
  - [Restoring Stock Defaults](#restoring-stock-defaults)
  - [Using Config Versions](#using-config-versions)

---

## Overview

`pipeline.yaml` controls every aspect of Atlas document processing: quality
thresholds, chunking strategy, retry behaviour, and cleanup rules. At startup
the file is validated; invalid config prevents the application from starting.

The stock template is `config/pipeline.yaml.example`. Copy it to
`config/pipeline.yaml` to customise — the live file is gitignored so
user-specific settings are never committed.

---

## Config Layering

```
┌─────────────────────────────┐
│  pipeline.yaml  (on disk)   │  ← YAML defaults, loaded at startup
└──────────┬──────────────────┘
           │ deep_merge()
┌──────────▼──────────────────┐
│  Active DB ConfigVersion    │  ← Created via UI / API; overrides YAML
└──────────┬──────────────────┘
           │
┌──────────▼──────────────────┐
│  Effective Config           │  ← What the pipeline actually uses
└─────────────────────────────┘
```

**Priority:** If an active DB config version exists, its `payload` is used as
the effective config. Otherwise, the YAML defaults are used. The DB version is
a complete snapshot (not a sparse patch) — it contains the full merged result
at creation time.

Every pipeline run records a `config_version_id` and `config_hash` in its
metadata for full traceability.

---

## Top-Level Keys

### `version`

| | |
|---|---|
| **Type** | `int` |
| **Required** | Yes |
| **Default** | — |
| **Validated** | Startup — `RuntimeError` if missing |

Schema version marker. Currently must be `1`.

```yaml
version: 1
```

---

### `thresholds`

Controls quality gating and routing decisions in the agentic loop.

| Key | Type | Default | Description |
|---|---|---|---|
| `judge_cutoff_refine` | `int` | `4` | Composite judge score below which a document routes to the Refine node. Score ≥ cutoff passes to Metadata. |
| `fail_fast_score` | `int` | `0` | Composite score at or below which the document is immediately **failed** (skipping refine entirely). `0` disables fail-fast. |
| `judge_dim_floors` | `map[str, int]` | `{}` | Per-dimension minimum scores. If **any** dimension falls below its floor, the document routes to Refine regardless of composite score. Set a dimension to `0` to disable its floor. |
| `cleanup_rejudge` | `bool` | `true` | When `true`, a document whose `formatting` sub-score is below cutoff but whose content dimensions (`faithfulness`, `cohesion`, `hallucination_risk`) are all acceptable is re-routed through **Cleanup** instead of Refine. Cycle-guarded: at most one cleanup-rejudge per document. |
| `judge_borderline_low` | `int` | `3` | Reserved for future borderline handling logic. Not consumed in current code. |
| `judge_borderline_high` | `int` | `4` | Reserved for future borderline handling logic. Not consumed in current code. |
| `refine_max_retries` | `int` | `2` | **Legacy location.** Prefer `limits.refine_max_retries`. Read as fallback. |
| `refine_min_preservation_ratio` | `float` | `0.85` | Minimum ratio of output length to input length after a refine pass. Outputs shorter than this ratio are rejected and the original text is kept. Prevents summarisation. |
| `refine_min_section_ratio` | `float` | `0.8` | Minimum ratio of output heading count to input heading count after a refine pass. Outputs with fewer than this fraction of headings are rejected (only triggers when input has ≥ 3 headings). Prevents section dropping. |

**Judge dimensions** (scored 1–5 each by the Judge LLM):
`faithfulness`, `formatting`, `cohesion`, `hallucination_risk`

```yaml
thresholds:
  judge_cutoff_refine: 4
  fail_fast_score: 0
  judge_dim_floors:
    faithfulness: 3
    formatting: 2
    cohesion: 2
    hallucination_risk: 3
  cleanup_rejudge: true
```

---

### `limits`

Hard caps on pipeline behaviour.

| Key | Type | Default | Description |
|---|---|---|---|
| `refine_max_retries` | `int` | `3` | Maximum Refine→Judge loop iterations before the document escalates to HITL review. Only successful refinements count; failed attempts are tracked separately with a 2× circuit-breaker hard cap. |
| `tier2_chunk_cap_per_document` | `int` | `25` | Maximum chunks per document that receive Tier-2 (expensive model) metadata enrichment. Also read from legacy key `metadata_tier2_cap_per_doc`. |
| `chunk_max_chars` | `int` | `1000` | Safety-valve maximum characters per chunk. Used primarily by the `paragraph` chunking strategy. |
| `max_context_tokens` | `int` | `16384` | Maximum estimated input tokens for a single full-document refine call. Documents exceeding this budget are refined sectionally. Based on refine model context window minus prompt overhead and output space. |
| `refine_max_section_tokens` | `int` | `6000` | Target maximum tokens per section when splitting a long document for sectional refinement. Sections are split on `##` headings, with secondary splits on `###` if a section still exceeds this limit. |

```yaml
limits:
  refine_max_retries: 3
  tier2_chunk_cap_per_document: 25
  chunk_max_chars: 1000
  max_context_tokens: 16384
  refine_max_section_tokens: 6000
```

---

### `normalize`

Post-pipeline markdown normalisation applied **after** the agentic loop
completes but **before** chunking/embedding. As of v0.6.0, normalize is
**formatting-only** — noise stripping (page numbers, repetitive lines) has
moved to `builtin_cleanup`.

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `true` | Toggle the normalisation pass. |

When enabled, `normalize_markdown()` applies:
1. **Heading spacing** — ensures blank line after headings
2. **List normalisation** — converts `1)` style to `1.`
3. **Blank line collapsing** — 3+ consecutive blank lines → 2

```yaml
normalize:
  enabled: true
```

---

### `chunking`

Controls how the final markdown is split into vector-store chunks.

| Key | Type | Default | Description |
|---|---|---|---|
| `strategy` | `str` | `"semantic"` | `"semantic"` (heading/table/list aware), `"paragraph"` (legacy max-chars splitter), or `"hierarchical"`. |
| `target_tokens` | `int` | `320` | Target token count per chunk (semantic strategy). |
| `max_tokens` | `int` | `400` | Hard ceiling token count (semantic strategy). |
| `qa` | `map` | *(see below)* | Chunk quality assurance bounds. Violations trigger automatic strategy fallback. |

**`chunking.qa` sub-keys:**

| Key | Type | Default | Description |
|---|---|---|---|
| `min_chunk_count` | `int` | `1` | Minimum chunks expected. Below this = QA failure. |
| `max_token_ratio_limit` | `float` | `1.25` | Max allowed ratio of largest chunk tokens to `max_tokens`. |
| `max_duplication_ratio` | `float` | `0.10` | Max fraction of chunk texts that are duplicates. |
| `min_coverage_ratio` | `float` | `0.80` | `sum(chunk chars) / source chars` — coverage floor. |

**Fallback chain:** `semantic → paragraph`, `hierarchical → paragraph`,
`paragraph → None` (no further fallback).

```yaml
chunking:
  strategy: semantic
  target_tokens: 320
  max_tokens: 400
  qa:
    min_chunk_count: 1
    max_token_ratio_limit: 1.25
    max_duplication_ratio: 0.10
    min_coverage_ratio: 0.80
```

---

### `frontier_fallback`

Controls automatic failover to cloud/frontier LLM providers when local
inference is under pressure.

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `false` | Master toggle. |
| `vram_percent_threshold` | `float` | `92.0` | VRAM usage % above which fallback triggers. |
| `queue_depth_threshold` | `int` | `2` | Inference queue depth above which fallback triggers. |

```yaml
frontier_fallback:
  enabled: false
  vram_percent_threshold: 92
  queue_depth_threshold: 2
```

---

### `cache`

Semantic query caching settings.

| Key | Type | Default | Description |
|---|---|---|---|
| `semantic_cache_enabled` | `bool` | `false` | Enable/disable semantic caching of queries. |
| `similarity_threshold` | `float` | `0.98` | Cosine similarity threshold for cache hits. |

> **Note:** Declared in the configuration schema but not yet wired to any
> consumer in the current codebase. Reserved for future use.

```yaml
cache:
  semantic_cache_enabled: false
  similarity_threshold: 0.98
```

---

### `privacy`

| Key | Type | Default | Description |
|---|---|---|---|
| `default_is_sensitive` | `bool` | `true` | Default sensitivity flag applied to documents. When `true`, the `PrivacyGuard` blocks routing to cloud/frontier APIs regardless of `frontier_fallback` settings. |

```yaml
privacy:
  default_is_sensitive: true
```

---

### `retry`

Exponential backoff configuration for external service calls. Each subsystem
(`llm`, `vectorstore`, `docling`) is configured independently.

| Key | Type | LLM Default | VectorStore Default | Docling Default | Description |
|---|---|---|---|---|---|
| `max_retries` | `int` | `3` | `3` | `2` | Maximum retry attempts. |
| `base_delay_s` | `float` | `2.0` | `1.0` | `3.0` | Initial backoff delay (seconds). |
| `max_delay_s` | `float` | `30.0` | `15.0` | `30.0` | Maximum delay cap. |

Delay formula: `min(base_delay_s * 2^attempt, max_delay_s) + jitter`

```yaml
retry:
  llm:
    max_retries: 3
    base_delay_s: 2.0
    max_delay_s: 30.0
  vectorstore:
    max_retries: 3
    base_delay_s: 1.0
    max_delay_s: 15.0
  docling:
    max_retries: 2
    base_delay_s: 3.0
    max_delay_s: 30.0
```

---

### `builtin_cleanup`

Toggles for automatic extraction-artifact fixes that run during the Cleanup
node **after** the five hardcoded transforms and **before** config-driven
cleanup rules. All toggles default to `true` (ON) when the section is absent
or a key is omitted.

| Key | Type | Default | Description |
|---|---|---|---|
| `html_unescape` | `bool` | `true` | Decode all HTML/XML character entities (`&amp;` → `&`, `&#8212;` → —, `&nbsp;` → non-breaking space). Uses Python's `html.unescape()`. |
| `fix_ligatures` | `bool` | `true` | Decompose common Unicode ligatures to ASCII equivalents (ﬁ → fi, ﬂ → fl, ﬀ → ff, ﬃ → ffi, ﬄ → ffl). |
| `strip_zero_width_chars` | `bool` | `true` | Remove zero-width and invisible Unicode characters (BOM, zero-width space/joiner/non-joiner, soft hyphen, word joiner, etc.). |
| `strip_page_numbers` | `bool` | `true` | Remove standalone page-number lines (e.g. `Page 3`, `— 12 —`, bare digits). |
| `strip_repetitive_lines` | `bool` | `false` | Remove lines that repeat ≥4 times in the document (headers/footers carried through from PDF extraction). |

```yaml
builtin_cleanup:
  html_unescape: true
  fix_ligatures: true
  strip_zero_width_chars: true
  strip_page_numbers: true
  strip_repetitive_lines: false
```

To disable a specific toggle:

```yaml
builtin_cleanup:
  fix_ligatures: false  # keep ligatures as-is
```

---

### `cleanup_rules`

An ordered list of rules that apply deterministic text transforms to the
markdown projection **after** ingestion but **before** the Judge node. This is
the primary mechanism for fixing OCR artefacts, removing boilerplate headers,
and normalising formatting issues that are predictable and pattern-based.

Rules are evaluated top-to-bottom; the **first matching rule** is applied (no
merging across rules in v1). An empty list disables config-driven cleanup
(built-in transforms still run).

```yaml
cleanup_rules: []
```

See the full reference below.

---

### `pdf_parser`

Controls how PDF files are parsed during the Ingest node. The layout parser
uses ONNX models (auto-downloaded from HuggingFace `InfiniFlow/deepdoc`) for
page-layout detection, OCR, and table structure recognition. When backend is
`auto` (the default), Docling is tried first and the layout parser is used as
a fallback if Docling fails. Selection is **whole-document** (not per-page).

| Key | Type | Default | Description |
|---|---|---|---|
| `backend` | `str` | `"auto"` | Parser selection: `auto` (Docling → layout fallback), `auto_layout` (layout → Docling fallback), `layout` (layout only, error on failure), `docling` (Docling only, skip layout). |
| `zoom` | `float` | `3.0` | Page rendering zoom factor — higher produces better OCR at the cost of speed and RAM. |
| `ocr_confidence_min` | `float` | `0.5` | Minimum mean OCR confidence (0.0–1.0) to accept layout parser output. Below this threshold, the result is rejected (and Docling used if `auto`/`auto_layout`). |
| `table_extraction` | `bool` | `true` | Enable table structure recognition. When disabled, tables are treated as regular text regions. |

```yaml
pdf_parser:
  backend: auto
  zoom: 3.0
  ocr_confidence_min: 0.5
  table_extraction: true
```

When the layout parser is used, the API response includes an `extraction_meta`
field with backend, OCR confidence, layout confidence, OCR coverage, and
scanned-document detection. The UI displays these metrics in the ingest result
card.

---

## Cleanup Rules — Full Reference

### Rule Structure

Each rule is a YAML mapping with these top-level keys:

| Key | Type | Required | Description |
|---|---|---|---|
| `name` | `str` | **Yes** | Unique identifier. Must match `^[a-zA-Z0-9_-]+$`. Used for deduplication when applying rules via API. |
| `match` | `map` | **Yes** | Filter conditions — determines if this rule fires for a given document. Empty `{}` = catch-all. |
| `steps` | `list[map]` | **Yes** | Ordered list of transforms. Must contain at least one step. |
| `tags` | `list[str]` | No | Labels consumed by routing logic for escalation/failure signals. Default: `[]`. |

---

### Match Block

Every non-null field in the match block must match the document's context for
the rule to fire. Comparison is **case-insensitive** for all ID fields.

| Field | Type | Default | Matching Logic |
|---|---|---|---|
| `tenant_id` | `str` | `null` (skip) | Exact equality (case-insensitive) |
| `project_id` | `str` | `null` (skip) | Exact equality (case-insensitive) |
| `corpus_id` | `str` | `null` (skip) | Exact equality (case-insensitive) |
| `mime_type` | `str` | `null` (skip) | Exact equality (case-insensitive) |
| `filename_pattern` | `str` | `null` (skip) | `fnmatch` glob pattern against the source filename |

**Match semantics:**
- All specified fields must match (AND logic).
- `null` / omitted fields are skipped (not checked).
- Empty match block `match: {}` matches **every** document (catch-all).

```yaml
# Match all PDFs in the "default" corpus
match:
  corpus_id: default
  mime_type: application/pdf

# Match files named like QRG_*.pdf (any tenant/corpus)
match:
  filename_pattern: "QRG_*.pdf"

# Catch-all (matches everything)
match: {}
```

---

### Step Kinds

Steps execute in declaration order. Each step receives the full markdown text
and returns the transformed text.

#### `strip_lines_matching`

Remove all lines that match a regex pattern.

| Param | Type | Required | Description |
|---|---|---|---|
| `pattern` | `str` | **Yes** | Python regex (applied per-line via `re.search`). |

```yaml
- kind: strip_lines_matching
  pattern: "^\\s*Page \\d+\\s*$"
```

#### `rewrite_pattern`

Regex search-and-replace across the full text.

| Param | Type | Required | Description |
|---|---|---|---|
| `pattern` | `str` | **Yes** | Python regex pattern. |
| `replacement` | `str` | **Yes** | Replacement string. Supports `\1`, `\2` backreferences. |

```yaml
- kind: rewrite_pattern
  pattern: "&amp;"
  replacement: "&"
```

#### `strip_headers_footers`

Remove the first/last N lines and/or lines matching patterns.

| Param | Type | Default | Description |
|---|---|---|---|
| `first_n` | `int` | `0` | Number of lines to strip from the beginning. |
| `last_n` | `int` | `0` | Number of lines to strip from the end. |
| `patterns` | `list[str]` | `[]` | Additional regex patterns — any matching line is removed. |

```yaml
- kind: strip_headers_footers
  first_n: 3
  last_n: 2
  patterns:
    - "^Page \\d+ of \\d+$"
    - "^CONFIDENTIAL"
```

#### `normalize_headings`

Convert setext-style headings to ATX-style. No parameters.

| Before | After |
|---|---|
| `Title\n=====` | `# Title` |
| `Subtitle\n-----` | `## Subtitle` |

```yaml
- kind: normalize_headings
```

#### `fix_numbered_headings`

Correct ATX heading levels based on dot-delimited section numbers.  Counts the
numeric segments in the identifier and sets the heading level accordingly.

| Param | Type | Default | Description |
|---|---|---|---|
| `max_level` | `int` | `6` | Maximum heading depth. Segments beyond this are clamped (e.g. 7 segments → H6). |

| Before | After |
|---|---|
| `## 1 Title` | `# 1 Title` |
| `## 1.11 Title` | `## 1.11 Title` |
| `## 1.1.8 Title` | `### 1.1.8 Title` |
| `## 1.2.3.4 Title` | `#### 1.2.3.4 Title` |

```yaml
- kind: fix_numbered_headings
```

Or with a custom max depth:

```yaml
- kind: fix_numbered_headings
  max_level: 4
```

> **Tip:** Place this step *after* `normalize_headings` so that setext headings
> are converted to ATX first, then their levels are corrected.

#### `merge_hardwrapped_paragraphs`

Join hard-wrapped lines (lines that don't end with a sentence terminator or
start a new structural element) into continuous paragraphs. No parameters.

```yaml
- kind: merge_hardwrapped_paragraphs
```

#### `fix_bullets`

Normalise bullet markers to a canonical character.

| Param | Type | Default | Description |
|---|---|---|---|
| `marker` | `str` | `"-"` | Target bullet character. All `*` and `+` bullets are converted to this. |

```yaml
- kind: fix_bullets
  marker: "-"
```

#### `html_unescape`

Decode all HTML/XML character entities in a single pass via Python's
`html.unescape()`. Handles named entities (`&amp;` → `&`, `&lt;` → `<`,
`&nbsp;` → non-breaking space), decimal (`&#8212;` → —), and hex
(`&#x2019;` → ') forms. No parameters required.

This is the recommended approach for Docling-extracted documents that contain
HTML-escaped characters instead of using multiple `rewrite_pattern` steps.

```yaml
- kind: html_unescape
```

---

### Tags and Routing

Tags are labels attached to a rule that influence **pipeline routing decisions**
after cleanup completes. They don't affect the transforms themselves.

| Tag | Routing Effect |
|---|---|
| `auto_fix_only` | Informational only — no routing impact. Signals the rule only applies safe transforms. |
| `hard_failure` | Routes the document to **FAILED** state immediately after cleanup. Use for documents that match a known-bad pattern (e.g. completely empty after OCR). |
| `suspicious_content` | Routes the document to **HITL** review after cleanup. Use for patterns that need human verification. |

Custom/non-standard tags are allowed but generate a startup validation
warning.

```yaml
tags:
  - auto_fix_only
```

---

### Rule Matching Behaviour

1. Rules are evaluated in **declaration order** (top of list first).
2. The **first** rule whose match block satisfies the document context is
   selected.
3. Only **one** rule is applied per document (no rule merging in v1).
4. If **no** rule matches, config-driven cleanup is skipped (built-in
   transforms still run).
5. A catch-all rule (`match: {}`) at the end of the list is a common pattern
   to ensure every document gets some cleanup.

---

### Examples

**Replace HTML entities in a specific corpus:**

```yaml
cleanup_rules:
  - name: replace_html_entities
    match:
      tenant_id: local
      project_id: default
      corpus_id: default
    steps:
      - kind: rewrite_pattern
        pattern: "&amp;"
        replacement: "&"
      - kind: rewrite_pattern
        pattern: "&lt;"
        replacement: "<"
      - kind: rewrite_pattern
        pattern: "&gt;"
        replacement: ">"
      - kind: rewrite_pattern
        pattern: "&quot;"
        replacement: "\""
    tags:
      - auto_fix_only
```

**Strip page numbers and legal headers from PDF docs, then normalise:**

```yaml
cleanup_rules:
  - name: legal_pdf_cleanup
    match:
      corpus_id: legal_docs
      mime_type: application/pdf
    steps:
      - kind: strip_headers_footers
        first_n: 3
        last_n: 2
        patterns:
          - "^Page \\d+ of \\d+$"
          - "^CONFIDENTIAL"
      - kind: merge_hardwrapped_paragraphs
      - kind: normalize_headings
      - kind: fix_bullets
    tags:
      - auto_fix_only

  - name: catch_all
    match: {}
    steps:
      - kind: normalize_headings
      - kind: fix_bullets
```

**Flag known-bad documents for hard failure:**

```yaml
cleanup_rules:
  - name: empty_ocr_fail
    match:
      mime_type: application/pdf
    steps:
      - kind: strip_lines_matching
        pattern: "^\\s*$"
    tags:
      - hard_failure
```

---

## Pipeline Flow

### Node Sequence

```
Ingest → Cleanup → Judge → Refine* → Metadata → Embeddings → Chunking → Commit
                     ↑        ↑         |
                     │        └─────────┘  (retry loop, max = limits.refine_max_retries)
                     └── cleanup-rejudge (max 1 cycle)
```

- **Ingest** — Document conversion via Docling or layout parser (ONNX) (PDF/DOCX/HTML → Markdown). Backend selected by `pdf_parser.backend` (`auto`/`auto_layout`/`layout`/`docling`; default `auto` tries Docling first, falls back to layout parser). Selection is whole-document, not per-page.
- **Cleanup** — Built-in transforms + config-driven rule engine.
- **Judge** — LLM grades quality on 4 dimensions (1–5 each). Per-dimension rationale for scores below 4.
- **Refine** — LLM rewrites the document to improve quality (if score < cutoff). Receives judge sub-scores, rationale, and iteration context. For documents exceeding `limits.max_context_tokens`, sectional refinement splits the document on headings and refines each section independently.
- **Metadata** — LLM generates tiered metadata tags.
- **Embeddings** — Vector generation via embedding model.
- **Chunking** — Split markdown into chunks (with QA checks + fallback).
- **Commit** — Upsert chunks + vectors to Qdrant.

**HITL** (Human-in-the-Loop) can be triggered at any decision point. The
pipeline pauses and waits for human review before resuming. HITL tasks
include rich context (judge sub-scores, rationale, score history, refine
attempts). Resume is guarded by `MAX_HITL_RESUMES=2` to prevent infinite
HITL→pipeline→HITL loops.

**Routing intelligence (v0.7.0):**
- **Score regression rollback** — if refine makes the score worse, the
  markdown is rolled back to the pre-refine version. Routes to Metadata
  if pre-refine score was acceptable, or to HITL otherwise.
- **Diminishing returns** — if a refine attempt produces no score change,
  the loop stops and escalates to HITL.
- **Failed refines don't burn retries** — only successful refinements count
  against `refine_max_retries`. A hard cap at 2× max retries prevents
  infinite failure loops.
- **Cleanup-rejudge cycle guard** — at most one cleanup→judge→cleanup
  cycle per document.
- **Context budget awareness (v0.7.1)** — before refine, the pipeline
  estimates document token count. Documents exceeding
  `limits.max_context_tokens` are refined sectionally (split on headings,
  each section refined independently, then reassembled). Two post-refine
  guards enforce quality: length preservation (≥85% of input) and
  section-count preservation (≥80% of headings).

---

### Routing Decisions

| Current Node | Condition | Next Node | Config Key |
|---|---|---|---|
| Ingest | Layout OCR confidence < 0.3 + `fail_fast_score` enabled | **Failed** | `thresholds.fail_fast_score` |
| Ingest | Docling health ≤ `fail_fast_score` | **Failed** | `thresholds.fail_fast_score` |
| Ingest | Default | Cleanup | — |
| Cleanup | `hard_failure` tag on matched rule | **Failed** | `cleanup_rules[].tags` |
| Cleanup | `suspicious_content` tag on matched rule | **HITL** | `cleanup_rules[].tags` |
| Cleanup | Default | Judge | — |
| Judge | Composite score ≤ `fail_fast_score` | **Failed** | `thresholds.fail_fast_score` |
| Judge | Any dimension < its floor | Refine | `thresholds.judge_dim_floors` |
| Judge | Formatting low + content OK + `cleanup_rejudge` (max 1 cycle) | Cleanup | `thresholds.cleanup_rejudge` |
| Judge | Score regressed after refine + pre-refine score ≥ cutoff | Metadata (with **rollback** to pre-refine markdown) | — |
| Judge | Score regressed after refine + pre-refine score < cutoff | **HITL** (with **rollback** to pre-refine markdown) | — |
| Judge | Score unchanged after refine (diminishing returns) | **HITL** | — |
| Judge | Composite < cutoff + retries left + doc fits context budget | Refine (full) | `thresholds.judge_cutoff_refine` |
| Judge | Composite < cutoff + retries left + doc exceeds context budget | Refine (sectional) | `limits.max_context_tokens` |
| Judge | Composite < cutoff + retries exhausted | **HITL** | `limits.refine_max_retries` |
| Judge | Score acceptable | Metadata | — |
| Refine | Always | Judge | — |
| Metadata → Embeddings → Chunking → Commit | Linear | Next in chain | — |

---

### Built-in Cleanup Transforms

These run **unconditionally** before config-driven rules, in this order:

| # | Transform | Description |
|---|---|---|
| 1 | `normalise_whitespace` | Collapse 3+ consecutive blank lines → 2 |
| 2 | `strip_broken_links` | Remove broken markdown links: `[text]()`, `[text](#)` |
| 3 | `repair_heading_hierarchy` | Demote headings that skip levels (e.g. H1 → H4 becomes H1 → H2) |
| 4 | `strip_trailing_whitespace` | Right-strip every line |
| 5 | *Static quality warnings* | Log warnings for leftover HTML tags, OCR whitespace artefacts, very short output (<50 chars). No mutations. |
| 6 | `builtin:html_unescape` | Decode HTML/XML entities. Configurable via `builtin_cleanup.html_unescape` (default: ON). |
| 7 | `builtin:fix_ligatures` | Decompose Unicode ligatures (ﬁ→fi, ﬂ→fl). Configurable via `builtin_cleanup.fix_ligatures` (default: ON). |
| 8 | `builtin:strip_zero_width_chars` | Strip invisible Unicode chars. Configurable via `builtin_cleanup.strip_zero_width_chars` (default: ON). |

---

### Docling Health Scoring

Computed automatically from ingest metadata. Not configurable.
Used by routing to trigger fail-fast when `fail_fast_score > 0`.

| Dimension | Source | Scoring (1–5) |
|---|---|---|
| Extraction method | `meta.extraction_method` | `embedded_text: 5`, `docling_default: 4`, `ocr: 3`, `unknown: 2` |
| Content length | `len(markdown)` | `0→1`, `<50→2`, `<200→3`, `<2000→4`, `≥2000→5` |
| Page rotation | `meta.pdf_preflight` | `mixed: 2`, `has_rotation: 3`, `none: 5` |
| Text-as-shapes | `meta.pdf_preflight` | `suspected: 1`, `not suspected: 5` |

**Composite score** = mean of all four dimensions (rounded to nearest int).

---

### Chunk Quality Assurance

After chunking, a QA check runs against `chunking.qa` bounds:

| Check | Bound Key | What Happens on Failure |
|---|---|---|
| Too few chunks | `min_chunk_count` | Automatic fallback to next strategy |
| Oversized chunk | `max_token_ratio_limit` | Automatic fallback to next strategy |
| Duplicate chunks | `max_duplication_ratio` | Automatic fallback to next strategy |
| Low coverage | `min_coverage_ratio` | Automatic fallback to next strategy |

**Fallback chain:** `semantic → paragraph → (no fallback)`.

---

## Admin API Endpoints

Config-related endpoints for managing pipeline configuration at runtime.

| Method | Path | Description |
|---|---|---|
| `GET` | `/admin/config/effective` | Returns the current effective config (YAML + DB merge) |
| `POST` | `/admin/reload-yaml` | Re-read pipeline.yaml from disk |
| `POST` | `/admin/config/restore-stock` | Deactivate all DB versions, revert to YAML defaults |
| `POST` | `/admin/config/validate-rules` | Validate a cleanup rules list without applying |
| `GET` | `/admin/config-versions` | List all config versions |
| `POST` | `/admin/config-versions` | Create a new config version (with optional patch + activate) |
| `POST` | `/admin/config-versions/{id}/activate` | Activate a specific version |
| `POST` | `/admin/cleanup-rules/apply` | Validate + apply a rule (creates new DB config version) |
| `POST` | `/admin/cleanup-rules/dry-run` | Test rules against a markdown sample without ingesting |
| `DELETE` | `/admin/cleanup-rules/{rule_name}` | Remove a rule by name (creates new DB config version) |
| `POST` | `/admin/cleanup-rules/suggest` | Ask the LLM to suggest a rule from sample markdown |

---

## Operational Workflows

### Editing a Rule in the UI

1. Navigate to **Admin → Cleanup & Feedback → Active cleanup rules**.
2. Expand the rule you want to edit.
3. Modify the YAML in the text area.
4. Click **Validate** to check syntax and schema.
5. Click **Save changes** — this creates a new DB config version with the
   updated rule. Active immediately, no restart required.

### Testing a Rule (Dry-Run)

1. In the Active Rules card, expand a rule and click **Dry-run**.
2. Paste a markdown sample in the text area that appears.
3. The system runs the full cleanup pipeline (built-in transforms + all
   config-driven rules) and shows:
   - Which rule matched
   - Whether the text changed
   - The cleaned output
   - Per-step fix counts

Alternatively, call the API directly:
```bash
curl -X POST http://localhost:18080/admin/cleanup-rules/dry-run \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "markdown_sample": "Hello &amp; world",
    "tenant_id": "local",
    "project_id": "default",
    "corpus_id": "default"
  }'
```

### Restoring Stock Defaults

1. Navigate to **Admin → Danger Zone**.
2. Click **Restore stock configuration**.
3. All DB config versions are deactivated. The pipeline reverts to
   `pipeline.yaml` on disk.

### Using Config Versions

Every change through the UI creates a versioned, immutable DB snapshot.
You can roll back to any previous version:

```bash
# List versions
curl http://localhost:18080/admin/config-versions \
  -H "Authorization: Bearer $TOKEN"

# Activate a specific version
curl -X POST http://localhost:18080/admin/config-versions/3/activate \
  -H "Authorization: Bearer $TOKEN"
```
