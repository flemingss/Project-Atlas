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
   - ``strip_repetitive_lines`` (**OFF by default**): remove short lines that
     repeat ≥N times (configurable threshold/max_chars).
"""

from __future__ import annotations

import html
import re
import logging
import unicodedata
from datetime import datetime, timezone
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


# Registry of builtin cleanup toggles: config key → function.
# Order matters — html_unescape should run first (entity decoding may
# produce characters that the later passes handle).
_BUILTIN_CLEANUP_REGISTRY: list[tuple[str, Any]] = [
    ("html_unescape", _builtin_html_unescape),
    ("fix_ligatures", _builtin_fix_ligatures),
    ("strip_zero_width_chars", _builtin_strip_zero_width),
    ("strip_page_numbers", _builtin_strip_page_numbers),
    ("strip_repetitive_lines", _builtin_strip_repetitive_lines),
]


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
            # Default to True (ON) when the key is absent — EXCEPT
            # strip_repetitive_lines which defaults to OFF for safety.
            default_on = toggle_key != "strip_repetitive_lines"

            # Layout parser already strips page numbers and handles ligatures
            if is_layout and toggle_key in ("strip_page_numbers",):
                continue

            if builtin_cfg.get(toggle_key, default_on):
                before = cleaned
                # strip_repetitive_lines accepts optional threshold/max_chars
                if toggle_key == "strip_repetitive_lines":
                    threshold = int(builtin_cfg.get("repetitive_line_threshold", 8))
                    max_ch = int(builtin_cfg.get("repetitive_line_max_chars", 80))
                    cleaned = handler(cleaned, threshold=threshold, max_chars=max_ch)
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
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            rules_applied=rules_applied,
            rules_failed=rules_failed,
            fix_counts=fix_counts,
            rule_tags=rule_tags,
        )
