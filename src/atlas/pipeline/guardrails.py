"""Deterministic LLM output guardrails (Layer 1).

Post-processing pass applied to raw LLM output before preservation
guardrails.  Strips conversational preamble/postamble, wrapping code
fences, and injected meta-commentary that models occasionally produce
despite explicit prompt instructions.
"""

from __future__ import annotations

import logging
import re

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Preamble lines: conversational openers at the start of output.
# Anchored to the very first non-blank line(s).
_PREAMBLE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"^(?:here\s+is|below\s+is|sure[,!]?\s|certainly[,!]?\s|of\s+course[,!]?\s"
        r"|i(?:'ve|'ve| have)\s+(?:made|improved|refined|cleaned|corrected|fixed|updated)"
        r"|the\s+(?:improved|refined|corrected|updated|cleaned)\s+(?:document|markdown|text|version)"
        r"|i\s+(?:noticed|found|see|observe|detected)"
        r"|as\s+(?:an?\s+ai|requested|instructed))",
        re.IGNORECASE,
    ),
]

# Postamble lines: conversational closers at the end of output.
_POSTAMBLE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"^(?:let\s+me\s+know|i\s+hope\s+this|feel\s+free|if\s+you\s+(?:need|want|have)"
        r"|please\s+(?:let|don't\s+hesitate)|don't\s+hesitate"
        r"|is\s+there\s+anything\s+else"
        r"|note:\s+i(?:'ve|'ve| have)\s+(?:made|kept|preserved)"
        r"|i\s+(?:made|kept|preserved)\s+(?:the\s+following|all|every)"
        r"|summary\s+of\s+(?:changes|improvements|modifications))",
        re.IGNORECASE,
    ),
]

# Meta-section headings that LLMs sometimes inject.
_META_HEADING_RE = re.compile(
    r"^#{1,3}\s+(?:summary\s+of\s+changes|improvements?\s+made"
    r"|changes?\s+(?:log|list|summary)|notes?\s+on\s+(?:changes|improvements)"
    r"|what\s+(?:was|i)\s+(?:changed|improved|fixed))\s*$",
    re.IGNORECASE,
)

# Whole-output markdown code fence wrapper.
_CODE_FENCE_WRAPPER_RE = re.compile(
    r"^```(?:markdown|md|text)?\s*\n(.*?)\n```\s*$",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def strip_llm_artifacts(text: str) -> str:
    """Strip common LLM conversational artifacts from refined output.

    This is a deterministic post-processing pass applied to raw LLM output
    before preservation guardrails.  It handles:

    1. Wrapping markdown code fences (```markdown … ```)
    2. Conversational preamble (first 1-3 lines)
    3. Conversational postamble (last 1-3 lines)
    4. Injected meta-commentary sections ("## Summary of Changes")

    Returns the cleaned text.  If stripping would reduce the text to <50%
    of the original, the original is returned unchanged (safety valve).
    """
    if not text or not text.strip():
        return text

    original_len = len(text.strip())
    cleaned = text

    # --- 1. Strip wrapping code fences ---
    m = _CODE_FENCE_WRAPPER_RE.match(cleaned.strip())
    if m:
        cleaned = m.group(1)
        _log.debug("strip_llm_artifacts: removed wrapping code fence")

    # --- 2. Strip preamble (up to 3 leading non-blank lines) ---
    lines = cleaned.split("\n")
    preamble_end = 0
    for i, line in enumerate(lines[:5]):  # scan first 5 lines max
        stripped = line.strip()
        if not stripped:
            continue  # skip blanks
        if any(p.search(stripped) for p in _PREAMBLE_PATTERNS):
            preamble_end = i + 1
        else:
            break  # stop at first non-matching non-blank line
    if preamble_end > 0:
        _log.debug("strip_llm_artifacts: removed %d preamble line(s)", preamble_end)
        lines = lines[preamble_end:]
        cleaned = "\n".join(lines)

    # --- 3. Strip postamble (up to 3 trailing non-blank lines) ---
    lines = cleaned.split("\n")
    postamble_start = len(lines)
    for i in range(len(lines) - 1, max(len(lines) - 6, -1), -1):
        stripped = lines[i].strip()
        if not stripped:
            continue
        if any(p.search(stripped) for p in _POSTAMBLE_PATTERNS):
            postamble_start = i
        else:
            break
    if postamble_start < len(lines):
        _log.debug(
            "strip_llm_artifacts: removed %d postamble line(s)",
            len(lines) - postamble_start,
        )
        lines = lines[:postamble_start]
        cleaned = "\n".join(lines)

    # --- 4. Strip injected meta-sections ---
    # Remove lines from a meta-heading through the next real heading or end
    out_lines: list[str] = []
    in_meta = False
    for line in cleaned.split("\n"):
        if _META_HEADING_RE.match(line.strip()):
            in_meta = True
            _log.debug("strip_llm_artifacts: removing meta-section '%s'", line.strip())
            continue
        if in_meta:
            # Exit meta-section when we hit a real heading
            if re.match(r"^#{1,6}\s+", line) and not _META_HEADING_RE.match(line.strip()):
                in_meta = False
                out_lines.append(line)
            # else: skip lines inside meta-section
            continue
        out_lines.append(line)
    cleaned = "\n".join(out_lines)

    # --- Safety valve ---
    # Protect against false-positive regex matches stripping real content.
    # Only trigger when: (a) the original is long enough for the ratio to
    # be meaningful (>200 chars), AND (b) stripping removed >50%.
    # For shorter texts (typical test/LLM outputs), artifact lines are
    # often a large fraction — stripping them is correct.
    cleaned = cleaned.strip()
    if (
        original_len > 200
        and len(cleaned) < original_len * 0.5
    ):
        _log.warning(
            "strip_llm_artifacts: stripping removed >50%% of content "
            "(%d→%d chars); keeping original",
            original_len,
            len(cleaned),
        )
        return text

    return cleaned


# ---------------------------------------------------------------------------
# Fact preservation (Layer 1b)
# ---------------------------------------------------------------------------

# A "fact token" is a digit-dominant word — at least three characters once
# thousands separators are removed, with digits making up at least half of
# it: phone-number groups (``4113``), part numbers (``090-15200-601`` →
# ``090``, ``15200``, ``601``), model names (``S600``), standards (``60068``),
# quantities (``360,000`` → ``360000``). These are the tokens a refine pass
# has no business changing, and the ones whose corruption is worst for
# retrieval — a confidently wrong number. Letter-dominant words that merely
# contain a digit (``syst3m``, ``Ov3rview``) are *not* facts: those are the
# OCR substitutions refine is supposed to repair. Case-folded so
# ``S600``/``s600`` match.
_THOUSANDS_SEP_RE = re.compile(r"(?<=\d)[,\u00a0\u202f ](?=\d{3}\b)")
_WORD_RE = re.compile(r"\b\w{3,}\b")


def _is_fact_token(token: str) -> bool:
    digits = sum(ch.isdigit() for ch in token)
    return digits > 0 and digits * 2 >= len(token)


def fact_tokens(text: str) -> set[str]:
    """Return the set of digit-dominant tokens (see module note) in *text*."""
    normalised = _THOUSANDS_SEP_RE.sub("", text)
    return {
        m.group().casefold()
        for m in _WORD_RE.finditer(normalised)
        if _is_fact_token(m.group())
    }


def dropped_facts(before: str, after: str, *, limit: int = 10) -> list[str]:
    """Fact tokens present in *before* but absent from *after*, sorted, capped.

    A token counts as preserved if it appears anywhere in *after* — moving,
    reformatting, or de-duplicating a repeated footer is fine; losing the
    only copy of ``4113`` is not.
    """
    missing = fact_tokens(before) - fact_tokens(after)
    return sorted(missing)[:limit]
