"""Tests for atlas.pipeline.cleanup — deterministic markdown transforms."""

from __future__ import annotations

import pytest

from atlas.pipeline.cleanup import (
    CleanupNode,
    _normalise_whitespace,
    _repair_heading_hierarchy,
    _static_checks,
    _strip_broken_links,
    _strip_trailing_whitespace,
)


# ---------------------------------------------------------------------------
# Individual transforms
# ---------------------------------------------------------------------------

def test_normalise_whitespace_collapses_blank_lines() -> None:
    text = "A\n\n\n\n\nB"
    assert _normalise_whitespace(text) == "A\n\nB"


def test_normalise_whitespace_preserves_double() -> None:
    text = "A\n\nB"
    assert _normalise_whitespace(text) == "A\n\nB"


def test_strip_broken_links_empty_href() -> None:
    assert _strip_broken_links("[click here]()") == "click here"


def test_strip_broken_links_hash_href() -> None:
    assert _strip_broken_links("[link](#)") == "link"


def test_strip_broken_links_preserves_real() -> None:
    text = "[docs](https://example.com)"
    assert _strip_broken_links(text) == text


def test_repair_heading_hierarchy_no_jump() -> None:
    text = "# H1\n## H2\n### H3"
    assert _repair_heading_hierarchy(text) == text


def test_repair_heading_hierarchy_jump() -> None:
    text = "# H1\n#### H4\n## H2"
    expected = "# H1\n## H4\n## H2"
    assert _repair_heading_hierarchy(text) == expected


def test_repair_heading_hierarchy_deep_jump() -> None:
    text = "# H1\n###### H6"
    expected = "# H1\n## H6"
    assert _repair_heading_hierarchy(text) == expected


def test_strip_trailing_whitespace() -> None:
    text = "hello   \nworld  "
    assert _strip_trailing_whitespace(text) == "hello\nworld"


# ---------------------------------------------------------------------------
# Static checks
# ---------------------------------------------------------------------------

def test_static_checks_detects_html() -> None:
    warnings = _static_checks("Some <div>html</div> here")
    assert any("leftover_html_tags" in w for w in warnings)


def test_static_checks_clean_markdown() -> None:
    warnings = _static_checks("# Heading\n\nClean paragraph text with enough words to exceed the minimum threshold.")
    assert len(warnings) == 0


def test_static_checks_very_short() -> None:
    warnings = _static_checks("Hi")
    assert any("very_short_output" in w for w in warnings)


# ---------------------------------------------------------------------------
# CleanupNode integration
# ---------------------------------------------------------------------------

async def test_cleanup_node_applies_transforms() -> None:
    node = CleanupNode()
    md = "# Title\n\n\n\n\n\nSome [broken]() link.   \n"
    result = await node.clean(markdown=md)

    assert "normalise_whitespace" in result.transforms_applied
    assert "strip_broken_links" in result.transforms_applied
    assert result.chars_before == len(md)
    assert result.chars_after <= result.chars_before
    assert "broken" not in result.cleaned_markdown or "()" not in result.cleaned_markdown


async def test_cleanup_node_no_op_on_clean_markdown() -> None:
    node = CleanupNode()
    md = "# Title\n\nA clean paragraph with enough content to avoid warnings."
    result = await node.clean(markdown=md)

    # No transforms needed.
    assert result.transforms_applied == []
    assert result.cleaned_markdown == md


async def test_cleanup_node_returns_cleanup_result_fields() -> None:
    node = CleanupNode()
    result = await node.clean(markdown="# Test\n\nBody text for testing purposes and content.")
    assert result.timestamp
    assert isinstance(result.transforms_applied, list)
    assert isinstance(result.warnings, list)
