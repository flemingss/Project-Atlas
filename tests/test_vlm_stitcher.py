"""Tests for atlas.vlm_ingest.stitcher — deterministic page stitching."""
from __future__ import annotations

from atlas.vlm_ingest.stitcher import (
    PageResult,
    StitchResult,
    _ends_with_table,
    _merge_heading_continuity,
    _merge_table_continuation,
    _starts_with_table_continuation,
    _strip_duplicate_lines,
    stitch_pages,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _page(num: int, md: str) -> PageResult:
    return PageResult(page_num=num, markdown=md)


# ---------------------------------------------------------------------------
# Basic stitching
# ---------------------------------------------------------------------------


class TestStitchBasic:
    def test_empty_pages(self):
        result = stitch_pages([])
        assert result.markdown == ""
        assert result.page_count == 0

    def test_single_page(self):
        result = stitch_pages([_page(0, "Hello world")])
        assert "Hello world" in result.markdown
        assert result.page_count == 1
        assert result.pages_processed == 1

    def test_page_comments_inserted(self):
        result = stitch_pages([
            _page(0, "Page one"),
            _page(1, "Page two"),
        ])
        assert "<!-- page 0 -->" in result.markdown
        assert "<!-- page 1 -->" in result.markdown

    def test_no_page_comments(self):
        result = stitch_pages([
            _page(0, "Page one"),
            _page(1, "Page two"),
        ], include_page_comments=False)
        assert "<!-- page" not in result.markdown
        assert "Page one" in result.markdown
        assert "Page two" in result.markdown

    def test_pages_sorted_by_number(self):
        result = stitch_pages([
            _page(2, "Third"),
            _page(0, "First"),
            _page(1, "Second"),
        ], include_page_comments=False)
        assert result.markdown.index("First") < result.markdown.index("Second")
        assert result.markdown.index("Second") < result.markdown.index("Third")

    def test_empty_pages_skipped(self):
        result = stitch_pages([
            _page(0, "Content"),
            _page(1, "   "),
            _page(2, "More content"),
        ])
        assert result.pages_processed == 2

    def test_trailing_newline(self):
        result = stitch_pages([_page(0, "Hello")])
        assert result.markdown.endswith("\n")
        assert not result.markdown.endswith("\n\n")


# ---------------------------------------------------------------------------
# Duplicate line removal
# ---------------------------------------------------------------------------


class TestDuplicateRemoval:
    def test_removes_repeated_footer_header(self):
        cleaned, n = _strip_duplicate_lines(
            "Content\nCompany Confidential",
            "Company Confidential\nMore content",
        )
        assert n == 1
        # The duplicate was stripped from curr — it still lives in prev
        assert "More content" in cleaned
        assert cleaned.strip().startswith("More content")

    def test_no_duplicates(self):
        cleaned, n = _strip_duplicate_lines("AAA", "BBB")
        assert n == 0
        assert cleaned == "BBB"

    def test_multiple_duplicate_lines(self):
        cleaned, n = _strip_duplicate_lines(
            "Content\nLine A\nLine B",
            "Line A\nLine B\nNew stuff",
        )
        assert n == 2
        assert "New stuff" in cleaned

    def test_full_stitch_deduplication(self):
        result = stitch_pages([
            _page(0, "Body text\nPage Footer"),
            _page(1, "Page Footer\nNext body"),
        ], include_page_comments=False)
        # "Page Footer" should appear only once
        assert result.markdown.count("Page Footer") == 1
        assert result.duplicate_lines_removed == 1


# ---------------------------------------------------------------------------
# Table merging
# ---------------------------------------------------------------------------


class TestTableMerge:
    def test_ends_with_table(self):
        assert _ends_with_table("Some text\n| a | b |\n| 1 | 2 |")
        assert not _ends_with_table("Some text\nNo table here")

    def test_starts_with_table_continuation(self):
        assert _starts_with_table_continuation("| 3 | 4 |\n| 5 | 6 |")
        assert not _starts_with_table_continuation("Normal text")

    def test_merge_split_table(self):
        prev = "# Title\n\n| h1 | h2 |\n|---|---|\n| a | b |"
        curr = "| c | d |\n| e | f |\n\nNext section"
        new_prev, new_curr, merged = _merge_table_continuation(prev, curr)
        assert merged
        assert "| c | d |" in new_prev
        assert "| e | f |" in new_prev
        assert "Next section" in new_curr

    def test_no_merge_when_no_table(self):
        prev, curr, merged = _merge_table_continuation("Text", "More text")
        assert not merged

    def test_full_stitch_table_merge(self):
        result = stitch_pages([
            _page(0, "| h1 | h2 |\n|---|---|\n| a | b |"),
            _page(1, "| c | d |\n\nEnd"),
        ], include_page_comments=False, strip_duplicates=False)
        assert result.tables_merged == 1
        # All table rows should be in a single block
        assert "| a | b |" in result.markdown
        assert "| c | d |" in result.markdown


# ---------------------------------------------------------------------------
# Heading merge
# ---------------------------------------------------------------------------


class TestHeadingMerge:
    def test_merge_duplicate_heading(self):
        cleaned, merged = _merge_heading_continuity(
            "Some text\n## Chapter 3",
            "## Chapter 3\nContent continues",
        )
        assert merged
        assert cleaned.count("Chapter 3") == 0 or cleaned.count("## Chapter 3") == 0
        assert "Content continues" in cleaned

    def test_no_merge_different_headings(self):
        cleaned, merged = _merge_heading_continuity(
            "## Chapter 3",
            "## Chapter 4\nContent",
        )
        assert not merged

    def test_no_merge_non_heading(self):
        cleaned, merged = _merge_heading_continuity(
            "Normal text",
            "## New heading\nContent",
        )
        assert not merged

    def test_full_stitch_heading_merge(self):
        result = stitch_pages([
            _page(0, "Intro\n## Results"),
            _page(1, "## Results\nData follows"),
        ], include_page_comments=False, strip_duplicates=False)
        assert result.headings_merged == 1


# ---------------------------------------------------------------------------
# Combined rules
# ---------------------------------------------------------------------------


class TestCombinedRules:
    def test_all_rules_together(self):
        result = stitch_pages([
            _page(0, "# Title\n\nIntro text\nREPEATING HEADER"),
            _page(1, "REPEATING HEADER\n\n## Section A\n\n| h | v |\n|---|---|\n| 1 | 2 |"),
            _page(2, "| 3 | 4 |\n\n## Section B\nContent"),
        ], include_page_comments=False)
        assert result.duplicate_lines_removed >= 1
        assert result.tables_merged == 1
        assert result.pages_processed == 3

    def test_stitch_result_fields(self):
        result = stitch_pages([_page(0, "A"), _page(1, "B")])
        assert isinstance(result, StitchResult)
        assert result.page_count == 2
        assert result.pages_processed == 2
        assert isinstance(result.markdown, str)
