"""Lightweight token estimation utilities for context-budget planning.

These are fast heuristics (no tokeniser dependency) used by the pipeline
to decide whether a document fits in the refine model's context window
and to compute dynamic ``max_tokens`` caps for LLM calls.
"""

from __future__ import annotations

import re

# Average characters-per-token for modern BPE tokenisers (GPT-4o /
# Qwen-2 family).  Empirically 3.5–4.2 on English markdown; we use the
# conservative (lower) end so estimates err on the side of caution.
_CHARS_PER_TOKEN = 3.7

# Regex for markdown headings (ATX-style: lines starting with 1–6 '#').
_HEADING_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)


def estimate_tokens(text: str) -> int:
    """Return an approximate token count for *text*.

    Uses a character-length heuristic (``len / 3.7``).  This is
    intentionally conservative — it will slightly *over*-count tokens
    so that context-budget checks have a safety margin.
    """
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


def count_headings(text: str) -> int:
    """Return the number of ATX-style markdown headings in *text*.

    Used by the section-count preservation guard to detect when the
    refine model drops entire sections.
    """
    return len(_HEADING_RE.findall(text))


def fits_in_context(
    input_text: str,
    max_context_tokens: int,
    *,
    prompt_overhead_tokens: int = 500,
    output_ratio: float = 1.15,
    max_output_tokens: int | None = None,
) -> bool:
    """Check whether a refine call can physically fit in one pass.

    Two separate ceilings have to hold, and they are not the same number:

    1. **Context window** — prompt plus expected output must fit in
       ``max_context_tokens``::

           prompt_tokens   = estimate_tokens(input_text) + prompt_overhead_tokens
           expected_output = estimate_tokens(input_text) * output_ratio
           total           = prompt_tokens + expected_output

    2. **Output ceiling** — refine rewrites the whole document, so the response
       is roughly as long as the input. ``max_output_tokens`` is usually far
       smaller than the context window (a model with a 1M context may cap
       responses at 48k), and it is the constraint that actually binds for
       long documents.

    Checking only (1) is a trap: setting ``max_context_tokens`` to a model's
    advertised context window looks correct and silently truncates every large
    refine, because the response hits the output cap long before the prompt
    runs out of context. The truncation surfaces only as a ``finish_reason``
    warning in the logs, and the preservation guard then rejects the result as
    dropped sections — which reads like a model quality problem rather than a
    misconfiguration.

    Returns False when either ceiling would be exceeded, so the caller falls
    back to sectional refinement.
    """
    input_tokens = estimate_tokens(input_text)
    expected_output = int(input_tokens * output_ratio)

    total_needed = input_tokens + prompt_overhead_tokens + expected_output
    if total_needed > max_context_tokens:
        return False

    if max_output_tokens is not None and expected_output > max_output_tokens:
        return False

    return True


def split_into_sections(text: str, max_section_tokens: int = 6000) -> list[str]:
    """Split markdown into sections suitable for independent refinement.

    Splits on top-level headings (``#`` or ``##``).  If a section still
    exceeds *max_section_tokens*, it is split further on ``###``
    headings.  Sections that are still too long after secondary
    splitting are kept as-is (the refine model will handle them with
    truncation risk, but the caller can decide to skip).

    Returns a list of markdown section strings.  Each string includes
    its heading line.  A preamble (content before the first heading) is
    returned as the first element if non-empty.
    """
    # Primary split on # or ##
    primary_re = re.compile(r"(?=^#{1,2}\s)", re.MULTILINE)
    raw_sections = primary_re.split(text)

    result: list[str] = []
    for section in raw_sections:
        section = section.strip()
        if not section:
            continue
        if estimate_tokens(section) <= max_section_tokens:
            result.append(section)
        else:
            # Secondary split on ###
            sub_re = re.compile(r"(?=^###\s)", re.MULTILINE)
            sub_sections = sub_re.split(section)
            for sub in sub_sections:
                sub = sub.strip()
                if sub:
                    result.append(sub)

    return result if result else [text]
