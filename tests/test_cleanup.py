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
    _builtin_html_unescape,
    _builtin_fix_ligatures,
    _builtin_strip_zero_width,
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


# ---------------------------------------------------------------------------
# Builtin extraction-artifact cleanup functions
# ---------------------------------------------------------------------------

def test_builtin_html_unescape_entities() -> None:
    assert _builtin_html_unescape("&amp;") == "&"
    assert _builtin_html_unescape("&lt;tag&gt;") == "<tag>"
    assert _builtin_html_unescape("&#8212;") == "\u2014"
    assert _builtin_html_unescape("no entities") == "no entities"


def test_builtin_fix_ligatures() -> None:
    assert _builtin_fix_ligatures("\ufb01le") == "file"
    assert _builtin_fix_ligatures("\ufb02oor") == "floor"
    assert _builtin_fix_ligatures("\ufb00") == "ff"
    assert _builtin_fix_ligatures("\ufb03") == "ffi"
    assert _builtin_fix_ligatures("\ufb04") == "ffl"
    assert _builtin_fix_ligatures("no ligatures") == "no ligatures"


def test_builtin_strip_zero_width() -> None:
    assert _builtin_strip_zero_width("hello\u200bworld") == "helloworld"
    assert _builtin_strip_zero_width("\ufeffBOM") == "BOM"
    assert _builtin_strip_zero_width("soft\u00adhyphen") == "softhyphen"
    assert _builtin_strip_zero_width("clean text") == "clean text"


# ---------------------------------------------------------------------------
# CleanupNode builtin toggles integration
# ---------------------------------------------------------------------------

async def test_cleanup_node_builtin_html_unescape_default_on() -> None:
    """html_unescape runs by default (no config needed)."""
    node = CleanupNode()
    md = "Veeam Backup &amp; Replication"
    result = await node.clean(markdown=md)
    assert "&amp;" not in result.cleaned_markdown
    assert "Veeam Backup & Replication" in result.cleaned_markdown
    assert "builtin:html_unescape" in result.transforms_applied


async def test_cleanup_node_builtin_ligatures_default_on() -> None:
    node = CleanupNode()
    md = "The \ufb01le was \ufb02at."
    result = await node.clean(markdown=md)
    assert "file" in result.cleaned_markdown
    assert "flat" in result.cleaned_markdown
    assert "builtin:fix_ligatures" in result.transforms_applied


async def test_cleanup_node_builtin_zero_width_default_on() -> None:
    node = CleanupNode()
    md = "\ufeffHello\u200bworld"
    result = await node.clean(markdown=md)
    assert result.cleaned_markdown.replace("\n", "") == "Helloworld"
    assert "builtin:strip_zero_width_chars" in result.transforms_applied


async def test_cleanup_node_builtin_can_be_disabled() -> None:
    """Setting a toggle to false skips that builtin."""
    node = CleanupNode()
    md = "Keep &amp; entities"
    config = {"builtin_cleanup": {"html_unescape": False}}
    result = await node.clean(markdown=md, config=config)
    assert "&amp;" in result.cleaned_markdown
    assert "builtin:html_unescape" not in result.transforms_applied


async def test_cleanup_node_builtin_partial_disable() -> None:
    """Only the disabled toggle is skipped; others still run."""
    node = CleanupNode()
    md = "\ufb01le &amp; \u200bstuff"
    config = {"builtin_cleanup": {"fix_ligatures": False}}
    result = await node.clean(markdown=md, config=config)
    # ligatures preserved
    assert "\ufb01" in result.cleaned_markdown
    # html_unescape and zero-width still ran
    assert "&amp;" not in result.cleaned_markdown
    assert "\u200b" not in result.cleaned_markdown
