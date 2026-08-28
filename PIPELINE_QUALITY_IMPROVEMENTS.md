# Pipeline Quality Improvements — Tracking & Adjudication

**Created**: 2026-03-01
**Status**: Layers 1–2 complete. Layer 3A open (`docs/ACTION_ITEMS.md` P2-02). Layer 3B superseded by the React editor (Phase 12C).
**Version**: shipped in the unreleased work on top of v0.8.0; this file is historical for Layers 1–2.

---

## Problem Statement

Production testing with hostile DoD PDFs revealed two categories of quality issues
that survive the current pipeline and either trigger unnecessary HITL escalation or
contaminate the final knowledge base:

1. **LLM Artifacts Leaking Into Output** — The Refine node's LLM occasionally injects
   conversational preamble/postamble, self-referential language ("Here is the improved
   document…"), or markdown code fences around the output. These artifacts are treated as
   document content by downstream nodes (Judge, Chunking, Embeddings).

2. **LLM Summarisation / Content Loss** — Despite the system prompt forbidding it, the
   Refine LLM sometimes summarises sections rather than preserving them verbatim. The
   preservation guardrail (≥85% length, ≥80% headings) catches extreme cases but misses
   subtle rewording that reduces information density.

3. **Hostile PDF Extraction Garbage** — Classification banners, TOC dot-leaders, garbled
   figure/table references, and repeated headers/footers from PDF extraction survive the
   deterministic Cleanup node. These degrade Judge scores and waste Refine capacity.

> **RAGFlow comparison**: Side-by-side testing confirmed RAGFlow produces *worse* results
> on the same hostile PDFs. The problem is inherent to these documents, not specific to
> Atlas. Atlas's pipeline is better positioned to improve because of the multi-stage
> architecture.

---

## Improvement Strategy — Three Layers

### Layer 1: Post-Refine Artifact Stripping + Prompt Hardening ← **DONE**

**Goal**: Eliminate LLM conversation artifacts from refined output.
**Effort**: ~1 day | **Risk**: Low (deterministic post-processing, prompt-only LLM change)

| Sub-task | Description | Status |
|----------|-------------|--------|
| 1a. Prompt hardening | Add explicit negative examples to `REFINE_SYSTEM_PROMPT` forbidding conversational preamble, postamble, code fences, meta-commentary | ✅ |
| 1b. Artifact stripping function | New `strip_llm_artifacts()` in `refine.py` — deterministic regex-based stripping applied to LLM output before guardrails | ✅ |
| 1c. Tests | 37 unit tests for artifact stripping (5 code fence, 12 preamble, 7 postamble, 4 meta-section, 3 combined, 6 safety/edge) | ✅ |
| 1d. Validation | Full test suite pass (485/485) + container rebuild | ✅ |

**Key patterns to strip**:
- Conversational preamble: "Here is the…", "Sure,", "I've made…", "Below is…"
- Conversational postamble: "Let me know…", "I hope this…", "Feel free to…"
- Wrapping code fences: ` ```markdown … ``` `
- Added meta-sections: "## Summary of Changes", "## Improvements Made"
- Self-referential: "As an AI…", "I noticed…", "I've cleaned up…"

---

### Layer 2: Cleanup Rules for Known Garbage Patterns

**Goal**: Strip common PDF extraction garbage deterministically before Judge runs.
**Effort**: ~0.5 day | **Risk**: Low (uses existing cleanup_rules engine)

| Sub-task | Description | Status |
|----------|-------------|--------|
| 2a. TOC dot-leaders | `strip_lines_matching` for `\.{4,}` patterns | ✅ (in personal rules) |
| 2b. Classification banners | Strip repeated `UNCLASSIFIED`, `CUI`, `FOUO` lines | ✅ (in personal rules) |
| 2c. Garbled figure refs | Strip lines like `Figure X-X.`, `Table X-X.` with no content | ✅ (in personal rules) |
| 2d. Repeated headers/footers | `strip_headers_footers` rule + `strip_repetitive_lines` builtin | ✅ |
| 2e. Tests | Verify rules fire correctly | ✅ (existing cleanup rule tests) |

**Implementation**: Add entries to `cleanup_rules: []` in the operator-local
`config/pipeline.yaml`. No code changes needed — the rules engine's 8 step handlers
(`strip_lines_matching`, `rewrite_pattern`, `strip_headers_footers`,
`normalize_headings`, `fix_numbered_headings`, `merge_hardwrapped_paragraphs`,
`fix_bullets`, `html_unescape`) already cover all required step kinds.

Stock config ships `cleanup_rules: []` deliberately — these patterns are
corpus-specific and would be wrong for someone else's documents. The worked
reference set lives in `personal_configs/`, which is where the "✅ (in personal
rules)" entries above point.

---

### Layer 3A: Strip & Re-judge HITL Enhancement (Recommended) — OPEN (`ACTION_ITEMS.md` P2-02)

**Goal**: Let HITL operators select sections to strip, then kick back to Judge for automatic
re-evaluation instead of full manual editing.
**Effort**: ~2-3 days | **Risk**: Medium

| Sub-task | Description | Status |
|----------|-------------|--------|
| 3Aa. API endpoint | `POST /admin/hitl/tasks/{id}/strip-and-rejudge` | ☐ |
| 3Ab. UI controls | Section selection checkboxes in HITL review view | ☐ |
| 3Ac. Routing | New re-judge path that bypasses refine | ☐ |
| 3Ad. Tests | E2E test for strip-and-rejudge flow | ☐ |

---

### Layer 3B: Document Editor with VLM Integration — SUPERSEDED

**Goal**: Standalone HTML/JS editor (PDF.js + CodeMirror 6) with LLM/VLM-assisted
refine, diff view, and accept/reject per-section. Replaces the deferred "VS Code-Style"
concept with a concrete architecture.

**Decision**: Planned as Phase 12 in `TECHNICAL_DESIGN.md`. Architecture: standalone
HTML/JS page served by FastAPI at `/editor` (Path A — no build toolchain, designed for
future React migration). See Phase 12A-12D for sub-tasks.

**Superseded**: the React migration happened in v0.8.0. The editor is now a page in
the React SPA at `/app/doc/:docId` (and `/app/run/:runId`); the standalone `/editor`
mount no longer exists. Do not implement 3B.

---

## Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-01 | Start with Layer 1 | Highest ROI, lowest risk. Directly addresses the most visible quality issue (LLM artifacts in output). |
| 2026-03-01 | Defer Layer 3B | Need to see how much Layers 1-2 reduce HITL volume before investing in editor UX. |
| 2026-03-03 | Layer 2 completed via cleanup rules optimization | Extended the rules engine to **8** step handlers (adding `fix_numbered_headings`, `strip_headers_footers`, `merge_hardwrapped_paragraphs`) and enabled the `strip_repetitive_lines` builtin. This did **not** change the shipped defaults: stock `config/pipeline.yaml` ships `cleanup_rules: []`. The worked ~10-rule reference set lives in `personal_configs/`, applied per operator. |
| 2026-03-03 | Layer 3B upgraded to Document Editor | VS Code-style concept replaced with concrete Phase 12 architecture (PDF.js + CodeMirror + VLM). |

---

## Test Results

| Date | Layer | Tests | Result | Notes |
|------|-------|-------|--------|-------|
| 2026-03-01 | Layer 1 | 485 total (37 new) | ✅ All pass | 37.46s, 0 failures, 0 skipped |

---

## Files Modified

| File | Layer | Change |
|------|-------|--------|
| `src/atlas/pipeline/refine.py` | 1a | Hardened `REFINE_SYSTEM_PROMPT` with 7 explicit negative examples (code fences, preamble, postamble, meta-sections, self-referential language) |
| `src/atlas/pipeline/refine.py` | 1b | Added `strip_llm_artifacts()` function (130+ lines) — deterministic post-refine stripping of code fences, preamble, postamble, meta-sections; safety valve for >200 char docs |
| `src/atlas/pipeline/refine.py` | 1b | Wired `strip_llm_artifacts()` into `_call_refine_model()` after `strip_reasoning_tags()` |
| `tests/test_llm_artifact_stripping.py` | 1c | 37 new unit tests across 6 test classes |
