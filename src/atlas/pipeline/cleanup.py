"""Cleanup pipeline node — deterministic markdown transforms.

Runs *before* the Judge node and applies lightweight, rule-based fixes to the
markdown projection produced by the Ingest node.  All transforms are
deterministic (no LLM calls) so they are fast, reproducible, and safe to retry.

Transforms (executed in order):
1. **Whitespace normalisation**: collapse runs of 3+ blank lines → 2.
2. **Broken link removal**: strip ``[text]()`` or ``[text](#)`` anchors.
3. **Header hierarchy repair**: demote disconnected heading jumps
   (e.g. ``# H1`` followed by ``#### H4`` → ``## H2``).
4. **Trailing-whitespace strip**: right-strip every line.
5. **Static quality checks**: detect leftover HTML tags, OCR artefacts, etc.
6. **Builtin extraction-artifact fixes** (configurable toggles via
   ``pipeline.yaml`` → ``builtin_cleanup:`` section):
   - ``html_unescape`` (ON): decode HTML/XML character entities.
   - ``fix_ligatures`` (ON): decompose Unicode ligatures (ﬁ→fi, ﬂ→fl, etc.).
   - ``strip_zero_width_chars`` (ON): remove zero-width/invisible Unicode chars.
   - ``strip_page_numbers`` (ON): remove standalone page-number lines.
   - ``strip_bullet_glyphs`` (ON): drop the bullet glyph the extractor keeps
     inside a list item (``- • text`` → ``- text``) and any Private Use Area
     glyphs (PDF symbol-font bullets that survive as ````).
   - ``normalize_superscripts`` (ON): rejoin flattened superscripts —
     ``1×10 -7`` → ``1×10^-7``, ``1 st`` → ``1st``.
   - ``dedupe_table_spans`` (ON): blank the repeats of a merged (spanned)
     cell that Docling's markdown export copies into every spanned column.
   - ``strip_repetitive_lines`` (**OFF by default**): remove short lines that
     repeat ≥N times (configurable threshold/max_chars).
   - ``strip_repeated_headings`` (**OFF by default**): drop later copies of a
     heading that appears ≥N times — per-page running headers that Docling
     promotes to ``##`` (configurable threshold).
"""

from __future__ import annotations

import html
import logging
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from atlas.pipeline.cleanup_rules import (
    DocContext,
    apply_rule,
    find_matching_rule,
    parse_rules,
)
from atlas.schemas import CleanupResult

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Individual transforms
# ---------------------------------------------------------------------------

def _normalise_whitespace(text: str) -> str:
    """Collapse 3+ consecutive blank lines into exactly 2."""
    return re.sub(r"\n{3,}", "\n\n", text)


def _strip_broken_links(text: str) -> str:
    """Remove markdown links whose href is empty or '#'."""
    # [text]() → text
    text = re.sub(r"\[([^\]]*)\]\(\s*\)", r"\1", text)
    # [text](#) → text
    text = re.sub(r"\[([^\]]*)\]\(\s*#\s*\)", r"\1", text)
    return text


def _repair_heading_hierarchy(text: str) -> str:
    """Demote headings that skip levels (e.g. H1 → H4 becomes H1 → H2).

    Only adjusts headings that jump by more than one level relative to the
    most recent heading.  Keeps the first heading's level as-is.
    """
    lines = text.split("\n")
    result: list[str] = []
    last_level = 0  # 0 = no heading seen yet

    for line in lines:
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            hashes = m.group(1)
            content = m.group(2)
            level = len(hashes)

            if last_level == 0:
                # First heading — keep as-is.
                last_level = level
            elif level > last_level + 1:
                # Jump is too big — clamp to last_level + 1.
                level = last_level + 1
            last_level = level
            result.append("#" * level + " " + content)
        else:
            result.append(line)

    return "\n".join(result)


def _strip_trailing_whitespace(text: str) -> str:
    """Right-strip every line."""
    return "\n".join(line.rstrip() for line in text.split("\n"))


# ---------------------------------------------------------------------------
# Builtin extraction-artifact fixes (configurable toggles, all ON by default)
# ---------------------------------------------------------------------------

# Common Unicode ligatures that PDF extractors emit as single codepoints.
# Decomposing them to ASCII equivalents improves search and matching.
_LIGATURE_MAP: dict[str, str] = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "st",  # long-s-t ligature
    "\ufb06": "st",
}
_LIGATURE_RE = re.compile("[" + "".join(_LIGATURE_MAP.keys()) + "]")

# Zero-width / invisible Unicode characters that serve no purpose in
# extracted markdown.  BOM, zero-width space/joiner/non-joiner, soft hyphen,
# word joiner, and several other common PDF extraction artefacts.
_ZERO_WIDTH_RE = re.compile(
    "["
    "\ufeff"   # BOM / ZWNBSP
    "\u200b"   # zero-width space
    "\u200c"   # zero-width non-joiner
    "\u200d"   # zero-width joiner
    "\u00ad"   # soft hyphen
    "\u2060"   # word joiner
    "\u2063"   # invisible separator
    "\u2064"   # invisible plus
    "\ufffe"   # non-character
    "]"
)

# Regex for standalone page-number lines.
_RE_PAGE_NUMBER = re.compile(
    r"^(?:page\s+)?\d+(?:\s*/\s*\d+|\s+of\s+\d+)?\s*$",
    flags=re.IGNORECASE,
)


def _builtin_html_unescape(text: str) -> str:
    """Decode all HTML/XML character entities (named, decimal, hex)."""
    return html.unescape(text)


def _builtin_fix_ligatures(text: str) -> str:
    """Decompose common Unicode ligatures to their ASCII equivalents."""
    return _LIGATURE_RE.sub(lambda m: _LIGATURE_MAP[m.group()], text)


def _builtin_strip_zero_width(text: str) -> str:
    """Strip zero-width and invisible Unicode characters."""
    return _ZERO_WIDTH_RE.sub("", text)


def _builtin_strip_page_numbers(text: str) -> str:
    """Remove standalone page-number lines.

    Matches lines like ``3``, ``Page 5``, ``2 / 10``, ``3 of 10``.
    """
    lines = text.split("\n")
    out: list[str] = []
    for ln in lines:
        if _RE_PAGE_NUMBER.match(ln.strip()):
            out.append("")  # blank instead of dropping to preserve line structure
        else:
            out.append(ln)
    return "\n".join(out)


def _builtin_strip_repetitive_lines(
    text: str,
    *,
    threshold: int = 8,
    max_chars: int = 80,
) -> str:
    """Remove non-empty lines ≤ *max_chars* that appear ≥ *threshold* times.

    This targets repeated headers, footers, and watermarks that PDF
    extractors replicate on every page.  The default threshold (8) is
    deliberately conservative to avoid dropping legitimate repeated
    content.
    """
    lines = text.split("\n")
    freq: dict[str, int] = {}
    for ln in lines:
        s = ln.strip()
        if not s or len(s) > max_chars:
            continue
        freq[s] = freq.get(s, 0) + 1
    repetitive = {s for s, c in freq.items() if c >= threshold}
    if not repetitive:
        return text
    return "\n".join("" if ln.strip() in repetitive else ln for ln in lines)


# Private Use Area codepoints (BMP block plus the two supplementary planes).
# PDF symbol fonts map their bullet/arrow glyphs here (Symbol's bullet is
# U+F0B7); extractors emit them verbatim. Nothing that belongs in a markdown
# projection lives in the PUA.
_PUA_RE = re.compile("[\ue000-\uf8ff\U000f0000-\U000ffffd\U00100000-\U0010fffd]")
# A typographic bullet sitting right after the markdown list marker: Docling
# emits the PDF's own bullet glyph as the first character of the item text,
# so every item arrives as "- • text". Plain "•" in prose is left alone.
_DOUBLE_BULLET_RE = re.compile(
    r"^([ \t]*(?:[-*+]|\d+[.)]))[ \t]+[\u2022\u2023\u25e6\u2043\u25aa\u25ab\u25cf\u25a0\u2219\u00b7][ \t]*(?=\S)",
    re.MULTILINE,
)
# "-  text" (two+ spaces after a list marker: what removing a glyph leaves).
_LIST_MARKER_GAP_RE = re.compile(r"^([ \t]*(?:[-*+]|\d+[.)]))[ \t]{2,}(?=\S)", re.MULTILINE)

# Superscripts flattened with a space: "±1×10 -7" (exponent), "1 st" (ordinal).
# The exponent form is anchored on "×10"/"x10" so a bare "10 -7" in prose is
# left alone.
_SCI_EXPONENT_RE = re.compile(r"([×x]10)[ \t]+([-−]?\d{1,3})\b")
_ORDINAL_RE = re.compile(r"\b(\d+)[ \t]+(st|nd|rd|th)\b")

# Markdown table rows. Escaped pipes inside cells are rare in extractor output
# and would break the naive split, so rows containing them are left alone.
_TABLE_ROW_RE = re.compile(r"^[ \t]*\|.*\|[ \t]*$")
_TABLE_SEP_RE = re.compile(r"^[ \t]*\|?(?:[ \t]*:?-{3,}:?[ \t]*\|)+[ \t]*(?::?-{3,}:?[ \t]*)?\|?[ \t]*$")
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*\S)[ \t]*$")


def _builtin_strip_bullet_glyphs(text: str) -> str:
    """Drop redundant bullet glyphs and the gap they leave after a list marker.

    ``- • text`` → ``- text``; any PUA glyph is removed; and ``-  text`` (the
    two-space residue an already-stripped glyph leaves — what Docling 2.76
    output looks like by the time it reaches this node) becomes ``- text``.
    """
    out = _DOUBLE_BULLET_RE.sub(r"\1 ", text)
    out = _PUA_RE.sub("", out)
    return _LIST_MARKER_GAP_RE.sub(r"\1 ", out)


def _builtin_normalize_superscripts(text: str) -> str:
    """Rejoin superscripts that the extractor flattened with a space.

    ``±1×10 -7`` → ``±1×10^-7`` and ``1 st`` → ``1st``. The caret form is
    chosen over Unicode superscripts because it survives search, tokenisation,
    and a plain-text diff.
    """
    out = _SCI_EXPONENT_RE.sub(r"\1^\2", text)
    return _ORDINAL_RE.sub(r"\1\2", out)


def _builtin_dedupe_table_spans(text: str, *, min_chars: int = 20) -> str:
    """Blank the repeats of a merged cell inside a markdown table row.

    Docling's markdown export writes a spanned cell into *every* column it
    covers, so a header ``Output BNCs`` spanning six columns arrives six
    times, and a footnote row spanning the table arrives once per column.
    A run of consecutive identical cells is reduced to its first occurrence;
    the others become whitespace of the same width so the row's alignment is
    untouched.

    Header rows (the row directly above the ``|---|`` separator) are deduped
    at any length — a header never legitimately repeats a label in adjacent
    columns. Body rows need *min_chars* per cell: short repeated values
    (``off | off``, ``Included | Included``, ``N/A | N/A``) are real data,
    while a spanned footnote or a long merged description is not.
    """
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if not _TABLE_ROW_RE.match(ln) or _TABLE_SEP_RE.match(ln) or "\\|" in ln:
            continue
        is_header = i + 1 < len(lines) and bool(_TABLE_SEP_RE.match(lines[i + 1]))
        floor = 1 if is_header else min_chars
        stripped = ln.strip()
        lead = ln[: len(ln) - len(ln.lstrip())]
        cells = stripped[1:-1].split("|")
        prev: str | None = None
        changed = False
        for j, cell in enumerate(cells):
            s = cell.strip()
            if not s:
                prev = None
                continue
            if prev is not None and s == prev and len(s) >= floor:
                cells[j] = " " * len(cell)
                changed = True
            prev = s
        if changed:
            lines[i] = f"{lead}|{'|'.join(cells)}|"
    return "\n".join(lines)


def _builtin_strip_repeated_headings(text: str, *, threshold: int = 3) -> str:
    """Drop later copies of a heading whose exact text appears ≥ *threshold* times.

    Per-page running headers ("SyncServer S600" on every page of a datasheet)
    come out of Docling as a heading per page, which then becomes the heading
    path of every chunk on that page. The first occurrence is kept. OFF by
    default: a manual that legitimately repeats ``## Notes`` per chapter
    would lose those headings.
    """
    lines = text.split("\n")
    heads = [
        (i, m.group(2).strip())
        for i, ln in enumerate(lines)
        if (m := _HEADING_RE.match(ln))
    ]
    freq = Counter(t for _, t in heads)
    seen: set[str] = set()
    for i, t in heads:
        if freq[t] < threshold:
            continue
        if t in seen:
            lines[i] = ""
        else:
            seen.add(t)
    return "\n".join(lines)


# Registry of builtin cleanup toggles: config key → function.
# Order matters — html_unescape should run first (entity decoding may
# produce characters that the later passes handle).
_BUILTIN_CLEANUP_REGISTRY: list[tuple[str, Any]] = [
    ("html_unescape", _builtin_html_unescape),
    ("fix_ligatures", _builtin_fix_ligatures),
    ("strip_zero_width_chars", _builtin_strip_zero_width),
    ("strip_bullet_glyphs", _builtin_strip_bullet_glyphs),
    ("strip_page_numbers", _builtin_strip_page_numbers),
    ("normalize_superscripts", _builtin_normalize_superscripts),
    ("dedupe_table_spans", _builtin_dedupe_table_spans),
    ("strip_repetitive_lines", _builtin_strip_repetitive_lines),
    ("strip_repeated_headings", _builtin_strip_repeated_headings),
]

# Toggles that default to OFF: each can remove legitimate content on the
# wrong document, so the operator opts in per corpus.
_BUILTIN_DEFAULT_OFF = frozenset({"strip_repetitive_lines", "strip_repeated_headings"})


# ---------------------------------------------------------------------------
# Static quality checks (produce warnings, not mutations)
# ---------------------------------------------------------------------------

_HTML_TAG_RE = re.compile(r"<(?!!)/?[a-zA-Z][^>]*>")
_OCR_ARTEFACT_RE = re.compile(r"[^\S\n]{4,}[A-Za-z]")  # long inline spaces followed by text


def _static_checks(text: str) -> list[str]:
    """Return a list of warning strings for common quality issues."""
    warnings: list[str] = []

    html_tags = _HTML_TAG_RE.findall(text)
    if html_tags:
        unique = sorted(set(html_tags))[:5]
        warnings.append(f"leftover_html_tags: {unique}")

    if _OCR_ARTEFACT_RE.search(text):
        warnings.append("possible_ocr_whitespace_artefacts")

    # Detect extremely short output (< 50 chars) which hints at a failed parse.
    stripped = text.strip()
    if stripped and len(stripped) < 50:
        warnings.append(f"very_short_output ({len(stripped)} chars)")

    return warnings


# ---------------------------------------------------------------------------
# CleanupNode
# ---------------------------------------------------------------------------

class CleanupNode:
    """Deterministic markdown cleanup — no LLM calls required."""

    async def clean(
        self,
        *,
        markdown: str,
        doc_context: dict[str, str] | None = None,
        config: dict[str, Any] | None = None,
    ) -> CleanupResult:
        """Apply built-in transforms, then config-driven rules if matched.

        Parameters
        ----------
        markdown:
            Raw markdown projection from the Ingest node.
        doc_context:
            Optional dict with keys ``tenant_id``, ``project_id``,
            ``corpus_id``, ``mime_type``, ``filename``.  Required for
            config-driven rule matching.
        config:
            Pipeline config dict (``pipeline.yaml``).  If it contains a
            ``cleanup_rules`` list, the engine will attempt to match and
            apply the first matching rule.
        """
        chars_before = len(markdown)
        transforms_applied: list[str] = []

        cleaned = markdown

        # 1. Whitespace normalisation
        before = cleaned
        cleaned = _normalise_whitespace(cleaned)
        if cleaned != before:
            transforms_applied.append("normalise_whitespace")

        # 2. Broken link removal
        before = cleaned
        cleaned = _strip_broken_links(cleaned)
        if cleaned != before:
            transforms_applied.append("strip_broken_links")

        # 3. Header hierarchy repair
        before = cleaned
        cleaned = _repair_heading_hierarchy(cleaned)
        if cleaned != before:
            transforms_applied.append("repair_heading_hierarchy")

        # 4. Trailing whitespace strip
        before = cleaned
        cleaned = _strip_trailing_whitespace(cleaned)
        if cleaned != before:
            transforms_applied.append("strip_trailing_whitespace")

        # 5. Static quality checks (warnings only)
        warnings = _static_checks(cleaned)

        # 6. Builtin extraction-artifact fixes (configurable toggles)
        builtin_cfg = (config or {}).get("builtin_cleanup", {})

        # Determine parse profile — layout parser already handles some artifacts
        parse_profile = (doc_context or {}).get("parse_profile", "")
        is_layout = parse_profile == "pdf_layout"

        for toggle_key, handler in _BUILTIN_CLEANUP_REGISTRY:
            # Default to True (ON) when the key is absent, except the
            # content-removing toggles in _BUILTIN_DEFAULT_OFF.
            default_on = toggle_key not in _BUILTIN_DEFAULT_OFF

            # Layout parser already strips page numbers and handles ligatures
            if is_layout and toggle_key in ("strip_page_numbers",):
                continue

            if builtin_cfg.get(toggle_key, default_on):
                before = cleaned
                # Parameterised toggles read their knobs from the same section.
                if toggle_key == "strip_repetitive_lines":
                    threshold = int(builtin_cfg.get("repetitive_line_threshold", 8))
                    max_ch = int(builtin_cfg.get("repetitive_line_max_chars", 80))
                    cleaned = handler(cleaned, threshold=threshold, max_chars=max_ch)
                elif toggle_key == "strip_repeated_headings":
                    threshold = int(builtin_cfg.get("repeated_heading_threshold", 3))
                    cleaned = handler(cleaned, threshold=threshold)
                elif toggle_key == "dedupe_table_spans":
                    min_chars = int(builtin_cfg.get("table_span_min_chars", 20))
                    cleaned = handler(cleaned, min_chars=min_chars)
                else:
                    cleaned = handler(cleaned)
                if cleaned != before:
                    transforms_applied.append(f"builtin:{toggle_key}")

        # --- Config-driven rule engine (Phase 7A) ---
        rules_applied: list[str] = []
        rules_failed: list[str] = []
        fix_counts: dict[str, int] = {}
        rule_tags: list[str] = []

        raw_rules = (config or {}).get("cleanup_rules", [])
        if raw_rules and doc_context:
            ctx = DocContext(
                tenant_id=doc_context.get("tenant_id", ""),
                project_id=doc_context.get("project_id", ""),
                corpus_id=doc_context.get("corpus_id", ""),
                mime_type=doc_context.get("mime_type", ""),
                filename=doc_context.get("filename", ""),
            )
            parsed = parse_rules(raw_rules)
            matched = find_matching_rule(parsed, ctx)
            if matched:
                result = apply_rule(matched, cleaned)
                rule_tags = result.tags
                fix_counts = result.fix_counts
                if result.steps_applied:
                    rules_applied.append(result.rule_name)
                    transforms_applied.extend(
                        f"rule:{result.rule_name}:{s}" for s in result.steps_applied
                    )
                    cleaned = result.markdown
                # Check for step errors (fix_count == -1 sentinel)
                if any(v == -1 for v in result.fix_counts.values()):
                    rules_failed.append(result.rule_name)

        return CleanupResult(
            cleaned_markdown=cleaned,
            transforms_applied=transforms_applied,
            warnings=warnings,
            chars_before=chars_before,
            chars_after=len(cleaned),
            timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            rules_applied=rules_applied,
            rules_failed=rules_failed,
            fix_counts=fix_counts,
            rule_tags=rule_tags,
        )
