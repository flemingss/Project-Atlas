"""Tests for cleanup behaviour with PDF_LAYOUT parse profile.

Verifies that CleanupNode correctly skips page-number stripping for
documents parsed via the layout parser (``parse_profile='pdf_layout'``),
since the layout parser already handles this during extraction.
"""

from __future__ import annotations

import pytest

from atlas.pipeline.cleanup import CleanupNode


# ---------------------------------------------------------------------------
# Layout profile: skip page-number stripping
# ---------------------------------------------------------------------------

async def test_cleanup_skips_page_numbers_for_layout_profile():
    """Page-number lines should NOT be stripped for pdf_layout profile."""
    node = CleanupNode()
    md = "# Title\n\nSome text\n\n3\n\nMore text"
    result = await node.clean(
        markdown=md,
        doc_context={"parse_profile": "pdf_layout"},
        config={"builtin_cleanup": {"strip_page_numbers": True}},
    )
    # "3" should be preserved — layout parser already handles page numbers
    assert "3" in result.cleaned_markdown


async def test_cleanup_strips_page_numbers_for_text_profile():
    """Page-number lines SHOULD be stripped for pdf_text profile."""
    node = CleanupNode()
    md = "# Title\n\nSome text\n\n3\n\nMore text"
    result = await node.clean(
        markdown=md,
        doc_context={"parse_profile": "pdf_text"},
        config={"builtin_cleanup": {"strip_page_numbers": True}},
    )
    # "3" on its own line matches the page-number regex and should be blanked
    lines = [ln.strip() for ln in result.cleaned_markdown.split("\n") if ln.strip()]
    assert "3" not in lines


async def test_cleanup_strips_page_numbers_for_scanned_profile():
    """Page numbers also stripped for pdf_scanned profile."""
    node = CleanupNode()
    md = "# Report\n\nContent here\n\nPage 5\n\nMore content"
    result = await node.clean(
        markdown=md,
        doc_context={"parse_profile": "pdf_scanned"},
        config={"builtin_cleanup": {"strip_page_numbers": True}},
    )
    lines = [ln.strip() for ln in result.cleaned_markdown.split("\n") if ln.strip()]
    assert "Page 5" not in lines


async def test_cleanup_strips_page_numbers_without_profile():
    """When no parse_profile is provided, default behaviour strips page numbers."""
    node = CleanupNode()
    md = "# Document\n\nText\n\n7\n\nEnd"
    result = await node.clean(
        markdown=md,
        doc_context={},
        config={"builtin_cleanup": {"strip_page_numbers": True}},
    )
    lines = [ln.strip() for ln in result.cleaned_markdown.split("\n") if ln.strip()]
    assert "7" not in lines


async def test_cleanup_layout_profile_still_applies_other_transforms():
    """Layout profile skips page numbers but still applies other cleanups."""
    node = CleanupNode()
    md = "# Title\n\n\n\n\nContent with [broken]()\n\n3\n\nExtra"
    result = await node.clean(
        markdown=md,
        doc_context={"parse_profile": "pdf_layout"},
        config={"builtin_cleanup": {"strip_page_numbers": True}},
    )
    # Whitespace normalisation should still fire
    assert "\n\n\n" not in result.cleaned_markdown
    # Broken link should be cleaned
    assert "[broken]()" not in result.cleaned_markdown
    # Page number "3" should be preserved (layout profile)
    assert "3" in result.cleaned_markdown


async def test_cleanup_layout_records_transforms():
    """Transforms list should reflect what was actually applied."""
    node = CleanupNode()
    md = "# Title   \n\nContent\n\n\n\n\nEnd"
    result = await node.clean(
        markdown=md,
        doc_context={"parse_profile": "pdf_layout"},
        config={"builtin_cleanup": {}},
    )
    # Whitespace normalisation and trailing whitespace should be recorded
    assert any("normalise_whitespace" in t for t in result.transforms_applied)
    assert any("strip_trailing_whitespace" in t for t in result.transforms_applied)
    # Page number stripping should NOT appear since it's skipped for layout
    assert not any("strip_page_numbers" in t for t in result.transforms_applied)


async def test_cleanup_page_number_disabled_in_config():
    """When strip_page_numbers is False in config, no stripping even for text profile."""
    node = CleanupNode()
    md = "# Title\n\n42\n\nContent"
    result = await node.clean(
        markdown=md,
        doc_context={"parse_profile": "pdf_text"},
        config={"builtin_cleanup": {"strip_page_numbers": False}},
    )
    assert "42" in result.cleaned_markdown
