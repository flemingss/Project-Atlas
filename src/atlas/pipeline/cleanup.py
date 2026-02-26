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
"""

from __future__ import annotations

import re
import logging
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
