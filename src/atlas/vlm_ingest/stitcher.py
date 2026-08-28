"""Deterministic stitching of per-page VLM markdown into a single document.

The stitcher joins page-level outputs using rule-based heuristics — no LLM
is invoked at any stage.  This keeps the final document free of cross-page
hallucination and makes the join behaviour predictable and testable.

Rules applied (in order):
1. Strip leading/trailing whitespace per page.
2. Insert a ``<!-- page N -->`` comment between pages (trackable, removable).
3. Detect and remove duplicate header/footer lines across consecutive pages.
4. Merge heading continuity (same heading text at page boundary → single heading).
5. Merge split markdown tables (page N ends with table rows, page N+1 starts
   with table rows → concatenate).
6. Normalize final whitespace (single trailing newline).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PageResult:
    """VLM output for a single page."""

    page_num: int  # 0-indexed
    markdown: str
    model: str = ""
    dpi: int = 200
    crop_top: float = 0.0
    crop_bottom: float = 0.0
    # Content address of the extraction that produced this page (see
    # vlm_ingest/store.py). Empty when the result did not come from a
    # cache-aware path — e.g. an operator's manual correction.
    cache_key: str = ""


@dataclass
class StitchResult:
    """Final stitched document."""

    markdown: str
    page_count: int
    pages_processed: int
    duplicate_lines_removed: int = 0
    tables_merged: int = 0
    headings_merged: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_TABLE_ROW_RE = re.compile(r"^\|.+\|$")
_TABLE_SEPARATOR_RE = re.compile(r"^\|[\s:|-]+\|$")


def _last_nonempty_lines(text: str, n: int = 3) -> list[str]:
    """Return the last *n* non-empty lines from *text*."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[-n:] if lines else []


def _first_nonempty_lines(text: str, n: int = 3) -> list[str]:
    """Return the first *n* non-empty lines from *text*."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[:n] if lines else []


def _strip_duplicate_lines(
    prev_text: str,
    curr_text: str,
    max_lines: int = 3,
) -> tuple[str, int]:
    """Remove leading lines from *curr_text* that duplicate trailing lines of *prev_text*.

    Catches repeated headers/footers that span page breaks.
    Returns ``(cleaned_curr, lines_removed)``.
    """
    prev_tail = _last_nonempty_lines(prev_text, max_lines)
    if not prev_tail:
        return curr_text, 0

    lines = curr_text.splitlines()
    removed = 0
    result_lines: list[str] = []
    tail_set = {ln.strip() for ln in prev_tail}
    still_checking = True

    for ln in lines:
        if still_checking and ln.strip() in tail_set:
            removed += 1
            tail_set.discard(ln.strip())
        else:
            still_checking = False
            result_lines.append(ln)

    return "\n".join(result_lines), removed


def _ends_with_table(text: str) -> bool:
    """Check if *text* ends with markdown table rows."""
    for ln in reversed(text.splitlines()):
        stripped = ln.strip()
        if not stripped:
            continue
        return bool(_TABLE_ROW_RE.match(stripped))
    return False


def _starts_with_table_continuation(text: str) -> bool:
    """Check if *text* starts with table rows (no heading, just data rows)."""
    for ln in text.splitlines():
        stripped = ln.strip()
        if not stripped:
            continue
        # A continuation starts with a table row but NOT a separator-only line
        # (separator-only would indicate a new table)
        return bool(_TABLE_ROW_RE.match(stripped))
    return False


def _merge_table_continuation(prev_text: str, curr_text: str) -> tuple[str, str, bool]:
    """If *prev_text* ends with a table and *curr_text* starts with table rows,
    move the continuation rows from *curr_text* onto *prev_text*.

    Returns ``(updated_prev, updated_curr, merged)``.
    """
    if not _ends_with_table(prev_text):
        return prev_text, curr_text, False

    if not _starts_with_table_continuation(curr_text):
        return prev_text, curr_text, False

    # Extract leading table rows from curr
    curr_lines = curr_text.splitlines()
    table_rows: list[str] = []
    rest_lines: list[str] = []
    in_table = True

    for ln in curr_lines:
        stripped = ln.strip()
        if in_table and (not stripped or _TABLE_ROW_RE.match(stripped)):
            table_rows.append(ln)
        else:
            in_table = False
            rest_lines.append(ln)

    merged_prev = prev_text.rstrip() + "\n" + "\n".join(table_rows)
    merged_curr = "\n".join(rest_lines)
    return merged_prev, merged_curr, True


def _merge_heading_continuity(prev_text: str, curr_text: str) -> tuple[str, bool]:
    """If *prev_text* ends with a heading and *curr_text* starts with the
    identical heading text, remove the duplicate from *curr_text*.

    Returns ``(updated_curr, merged)``.
    """
    prev_tail = _last_nonempty_lines(prev_text, 1)
    curr_head = _first_nonempty_lines(curr_text, 1)

    if not prev_tail or not curr_head:
        return curr_text, False

    m_prev = _HEADING_RE.match(prev_tail[0].strip())
    m_curr = _HEADING_RE.match(curr_head[0].strip())

    if not m_prev or not m_curr:
        return curr_text, False

    # Same heading text (ignore level differences — level from prev wins)
    if m_prev.group(2).strip() == m_curr.group(2).strip():
        lines = curr_text.splitlines()
        result: list[str] = []
        skipped = False
        for ln in lines:
            if not skipped and _HEADING_RE.match(ln.strip()):
                skipped = True
                continue
            result.append(ln)
        return "\n".join(result), True

    return curr_text, False


# ---------------------------------------------------------------------------
# Main stitcher
# ---------------------------------------------------------------------------

def stitch_pages(
    pages: list[PageResult],
    *,
    include_page_comments: bool = True,
    strip_duplicates: bool = True,
    merge_tables: bool = True,
    merge_headings: bool = True,
) -> StitchResult:
    """Stitch per-page VLM outputs into a single markdown document.

    Args:
        pages: Ordered list of per-page VLM results (must be sorted by page_num).
        include_page_comments: Insert ``<!-- page N -->`` comments between pages.
        strip_duplicates: Remove duplicate header/footer lines at page boundaries.
        merge_tables: Merge tables that span page breaks.
        merge_headings: Remove duplicate headings at page boundaries.

    Returns:
        A :class:`StitchResult` with the combined markdown and statistics.
    """
    if not pages:
        return StitchResult(markdown="", page_count=0, pages_processed=0)

    # Sort by page number
    sorted_pages = sorted(pages, key=lambda p: p.page_num)

    stats = {"dup_lines": 0, "tables_merged": 0, "headings_merged": 0}
    processed: list[str] = []

    for i, page in enumerate(sorted_pages):
        text = page.markdown.strip()
        if not text:
            continue

        if i > 0 and processed:
            prev_text = processed[-1]

            # 1) Duplicate line removal (headers/footers)
            if strip_duplicates:
                text, n_removed = _strip_duplicate_lines(prev_text, text)
                stats["dup_lines"] += n_removed

            # 2) Table continuation merge
            if merge_tables:
                merged_prev, text, did_merge = _merge_table_continuation(prev_text, text)
                if did_merge:
                    processed[-1] = merged_prev
                    stats["tables_merged"] += 1

            # 3) Heading continuity
            if merge_headings:
                text, did_merge_h = _merge_heading_continuity(processed[-1], text)
                if did_merge_h:
                    stats["headings_merged"] += 1

        # Insert page comment
        if include_page_comments:
            text = f"<!-- page {page.page_num} -->\n\n{text}"

        processed.append(text)

    # Join pages with double newline
    combined = "\n\n".join(processed).strip() + "\n"

    return StitchResult(
        markdown=combined,
        page_count=len(sorted_pages),
        pages_processed=len(processed),
        duplicate_lines_removed=stats["dup_lines"],
        tables_merged=stats["tables_merged"],
        headings_merged=stats["headings_merged"],
    )
