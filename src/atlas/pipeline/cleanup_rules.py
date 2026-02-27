"""Config-driven cleanup rules engine — deterministic, first-match-wins.

Allows operators to define per-corpus / per-project / per-tenant cleanup
rules in ``pipeline.yaml`` (and thus versioned via ``config_versions``).
Rules are evaluated in declaration order; the **first matching rule** is
applied (no merging in v1).

Each rule has:
- **match** block: zero or more filters (tenant_id, project_id, corpus_id,
  mime_type, filename_pattern).  An empty match block acts as a catch-all.
- **steps** list: deterministic text transforms executed in order.
- **tags** list (optional): labels consumed by downstream routing
  (e.g. ``suspicious_content``, ``hard_failure``, ``auto_fix_only``).

Step types
----------
- ``strip_lines_matching``:  remove lines matching a regex.
- ``rewrite_pattern``:       regex search-and-replace.
- ``strip_headers_footers``: remove first/last *n* lines or lines matching
  a list of patterns (useful for recurring page headers/footers in PDFs).
- ``normalize_headings``:    force ATX-style headings (``# H1`` not ``H1\\n===``).
- ``merge_hardwrapped_paragraphs``:  join lines not separated by a blank line
  into a single paragraph (common OCR artefact).
- ``fix_bullets``:           normalise mixed bullet styles (``*``, ``-``, ``+``)
  to a single canonical marker.
- ``html_unescape``:         decode all HTML/XML character entities
  (``&amp;`` → ``&``, ``&#8212;`` → ``—``, ``&nbsp;`` → " ", etc.)
  in a single pass via :func:`html.unescape`.
"""

from __future__ import annotations

import fnmatch
import html
import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RuleMatch:
    """Conditions that must *all* hold for a rule to match a document."""

    tenant_id: str | None = None
    project_id: str | None = None
    corpus_id: str | None = None
    mime_type: str | None = None
    filename_pattern: str | None = None  # fnmatch/glob pattern


@dataclass(frozen=True)
class RuleStep:
    """A single deterministic transform inside a cleanup rule.

    ``kind`` is one of the supported step types.  ``params`` carries
    kind-specific configuration (e.g. ``{"pattern": "^Page \\d+$"}``).
    """

    kind: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CleanupRule:
    """One complete cleanup rule — match + steps + tags."""

    name: str
    match: RuleMatch
    steps: list[RuleStep]
    tags: list[str] = field(default_factory=list)


@dataclass
class RuleApplicationResult:
    """Outcome of applying a single rule to markdown text."""

    rule_name: str
    tags: list[str]
    steps_applied: list[str]
    fix_counts: dict[str, int]
    markdown: str


# ---------------------------------------------------------------------------
# Parsing helpers — build typed objects from raw config dicts
# ---------------------------------------------------------------------------

def parse_rules(raw: list[dict[str, Any]]) -> list[CleanupRule]:
    """Parse a list of raw YAML/JSON rule dicts into :class:`CleanupRule` objects.

    Silently skips rules that are structurally invalid (logs a warning).
    """
    rules: list[CleanupRule] = []
    for idx, entry in enumerate(raw):
        try:
            match_raw = entry.get("match", {}) or {}
            match = RuleMatch(
                tenant_id=match_raw.get("tenant_id"),
                project_id=match_raw.get("project_id"),
                corpus_id=match_raw.get("corpus_id"),
                mime_type=match_raw.get("mime_type"),
                filename_pattern=match_raw.get("filename_pattern"),
            )
            steps: list[RuleStep] = []
            for s in entry.get("steps", []):
                if isinstance(s, str):
                    steps.append(RuleStep(kind=s))
                elif isinstance(s, dict):
                    steps.append(RuleStep(kind=s.get("kind", ""), params={k: v for k, v in s.items() if k != "kind"}))
                else:
                    log.warning("cleanup_rules[%d]: ignoring non-dict/str step: %r", idx, s)
            tags = list(entry.get("tags", []))
            name = str(entry.get("name", f"rule_{idx}"))
            rules.append(CleanupRule(name=name, match=match, steps=steps, tags=tags))
        except Exception:
            log.warning("cleanup_rules[%d]: failed to parse rule, skipping", idx, exc_info=True)
    return rules


# ---------------------------------------------------------------------------
# Matching — first-match-wins
# ---------------------------------------------------------------------------

@dataclass
class DocContext:
    """Lightweight descriptor of the document being cleaned."""

    tenant_id: str = ""
    project_id: str = ""
    corpus_id: str = ""
    mime_type: str = ""
    filename: str = ""


def _matches(rule: CleanupRule, ctx: DocContext) -> bool:
    """Return True if *rule* matches the document described by *ctx*.

    Matching logic: every non-``None`` field in the rule's ``match`` block
    must equal the corresponding ``ctx`` field (case-insensitive for IDs,
    fnmatch for filename_pattern).  An empty match block matches everything.
    """
    m = rule.match
    if m.tenant_id is not None and m.tenant_id.lower() != ctx.tenant_id.lower():
        return False
    if m.project_id is not None and m.project_id.lower() != ctx.project_id.lower():
        return False
    if m.corpus_id is not None and m.corpus_id.lower() != ctx.corpus_id.lower():
        return False
    if m.mime_type is not None and m.mime_type.lower() != ctx.mime_type.lower():
        return False
    if m.filename_pattern is not None:
        if not fnmatch.fnmatch(ctx.filename.lower(), m.filename_pattern.lower()):
            return False
    return True


def find_matching_rule(rules: list[CleanupRule], ctx: DocContext) -> CleanupRule | None:
    """Return the first rule whose match block satisfies *ctx*, or ``None``."""
    for rule in rules:
        if _matches(rule, ctx):
            return rule
    return None


# ---------------------------------------------------------------------------
# Step execution — deterministic transforms
# ---------------------------------------------------------------------------

def _step_strip_lines_matching(text: str, params: dict[str, Any]) -> tuple[str, int]:
    """Remove lines matching ``params["pattern"]`` regex."""
    pattern = params.get("pattern", "")
    if not pattern:
        return text, 0
    regex = re.compile(pattern)
    lines = text.split("\n")
    kept: list[str] = []
    removed = 0
    for line in lines:
        if regex.search(line):
            removed += 1
        else:
            kept.append(line)
    return "\n".join(kept), removed


def _step_rewrite_pattern(text: str, params: dict[str, Any]) -> tuple[str, int]:
    """Regex search-and-replace.  ``params`` keys: ``pattern``, ``replacement``."""
    pattern = params.get("pattern", "")
    replacement = params.get("replacement", "")
    if not pattern:
        return text, 0
    result, count = re.subn(pattern, replacement, text)
    return result, count


def _step_strip_headers_footers(text: str, params: dict[str, Any]) -> tuple[str, int]:
    """Remove recurring page headers/footers.

    ``params`` keys (all optional):
    - ``first_n``: remove first *n* lines.
    - ``last_n``:  remove last *n* lines.
    - ``patterns``: list of regex patterns — remove any matching line.
    """
    lines = text.split("\n")
    removed = 0

    first_n = int(params.get("first_n", 0))
    last_n = int(params.get("last_n", 0))
    patterns: list[str] = params.get("patterns", [])

    if first_n > 0:
        removed += min(first_n, len(lines))
        lines = lines[first_n:]
    if last_n > 0:
        removed += min(last_n, len(lines))
        lines = lines[:-last_n] if last_n < len(lines) else []

    if patterns:
        compiled = [re.compile(p) for p in patterns]
        kept: list[str] = []
        for line in lines:
            if any(r.search(line) for r in compiled):
                removed += 1
            else:
                kept.append(line)
        lines = kept

    return "\n".join(lines), removed


def _step_normalize_headings(text: str, _params: dict[str, Any]) -> tuple[str, int]:
    """Convert setext-style headings (underline with ``===`` or ``---``) to ATX-style."""
    lines = text.split("\n")
    result: list[str] = []
    count = 0
    i = 0
    while i < len(lines):
        if (
            i + 1 < len(lines)
            and lines[i].strip()
            and re.match(r"^={3,}$", lines[i + 1].strip())
        ):
            result.append("# " + lines[i].strip())
            count += 1
            i += 2
        elif (
            i + 1 < len(lines)
            and lines[i].strip()
            and re.match(r"^-{3,}$", lines[i + 1].strip())
        ):
            result.append("## " + lines[i].strip())
            count += 1
            i += 2
        else:
            result.append(lines[i])
            i += 1
    return "\n".join(result), count


def _step_merge_hardwrapped(text: str, _params: dict[str, Any]) -> tuple[str, int]:
    """Join hard-wrapped lines (no blank line between them) into paragraphs.

    Leaves blank lines as paragraph separators.  Preserves lines that look
    like headings, list items, or code fences.
    """
    lines = text.split("\n")
    merged: list[str] = []
    count = 0
    buffer: list[str] = []

    def _flush() -> None:
        nonlocal count
        if buffer:
            if len(buffer) > 1:
                count += len(buffer) - 1
            merged.append(" ".join(buffer))
            buffer.clear()

    for line in lines:
        stripped = line.strip()
        # Structural lines break the buffer
        if not stripped or stripped.startswith("#") or re.match(r"^[-*+] ", stripped) or stripped.startswith("```"):
            _flush()
            merged.append(line)
        else:
            buffer.append(stripped)

    _flush()
    return "\n".join(merged), count


def _step_fix_bullets(text: str, params: dict[str, Any]) -> tuple[str, int]:
    """Normalise mixed bullet markers to a canonical one (default ``-``)."""
    canonical = params.get("marker", "-")
    # Match lines starting with * or + followed by space (not inside code fences)
    count = 0
    lines = text.split("\n")
    result: list[str] = []
    in_fence = False
    for line in lines:
        if line.strip().startswith("```"):
            in_fence = not in_fence
        if not in_fence:
            new_line, n = re.subn(r"^(\s*)[*+] ", rf"\1{canonical} ", line)
            count += n
            result.append(new_line)
        else:
            result.append(line)
    return "\n".join(result), count


def _step_html_unescape(text: str, _params: dict[str, Any]) -> tuple[str, int]:
    """Decode all HTML/XML character entities via :func:`html.unescape`.

    Handles named entities (``&amp;``, ``&lt;``, ``&nbsp;``), decimal
    (``&#8212;``), and hex (``&#x2019;``) forms in a single pass.
    No parameters required.

    .. note::
        This step is redundant when the ``html_unescape`` builtin cleanup
        toggle is enabled (ON by default).  The builtin runs *before*
        user-defined rules, so enabling both is harmless (idempotent).
    """
    from atlas.pipeline.cleanup import _builtin_html_unescape

    result = _builtin_html_unescape(text)
    # Count changes by diffing on '&' — each entity starts with '&'
    # A more precise count: number of entity occurrences in original
    import re as _re
    count = len(_re.findall(r"&(?:#[xX]?[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*);?", text))
    # But only count the ones that actually changed
    if result == text:
        count = 0
    return result, count


_STEP_REGISTRY: dict[str, Any] = {
    "strip_lines_matching": _step_strip_lines_matching,
    "rewrite_pattern": _step_rewrite_pattern,
    "strip_headers_footers": _step_strip_headers_footers,
    "normalize_headings": _step_normalize_headings,
    "merge_hardwrapped_paragraphs": _step_merge_hardwrapped,
    "fix_bullets": _step_fix_bullets,
    "html_unescape": _step_html_unescape,
}


# ---------------------------------------------------------------------------
# Public API — apply matched rule
# ---------------------------------------------------------------------------

def apply_rule(rule: CleanupRule, markdown: str) -> RuleApplicationResult:
    """Execute all steps in *rule* sequentially against *markdown*.

    Returns a :class:`RuleApplicationResult` with per-step fix counts.
    """
    steps_applied: list[str] = []
    fix_counts: dict[str, int] = {}
    text = markdown

    for step in rule.steps:
        handler = _STEP_REGISTRY.get(step.kind)
        if handler is None:
            log.warning("Unknown cleanup-rule step kind '%s' in rule '%s' – skipping", step.kind, rule.name)
            continue
        try:
            new_text, fixes = handler(text, step.params)
            if new_text != text:
                steps_applied.append(step.kind)
                fix_counts[step.kind] = fix_counts.get(step.kind, 0) + fixes
                text = new_text
            else:
                fix_counts.setdefault(step.kind, 0)
        except Exception:
            log.warning("Step '%s' in rule '%s' raised – skipping", step.kind, rule.name, exc_info=True)
            fix_counts[step.kind] = fix_counts.get(step.kind, 0) - 1  # sentinel for error

    return RuleApplicationResult(
        rule_name=rule.name,
        tags=list(rule.tags),
        steps_applied=steps_applied,
        fix_counts=fix_counts,
        markdown=text,
    )
