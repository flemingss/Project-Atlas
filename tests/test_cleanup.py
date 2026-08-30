"""Tests for atlas.pipeline.cleanup — deterministic markdown transforms."""

from __future__ import annotations

from atlas.pipeline.cleanup import (
    CleanupNode,
    _builtin_dedupe_table_spans,
    _builtin_fix_ligatures,
    _builtin_html_unescape,
    _builtin_normalize_superscripts,
    _builtin_strip_bullet_glyphs,
    _builtin_strip_repeated_headings,
    _builtin_strip_zero_width,
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


# ---------------------------------------------------------------------------
# Builtin fixes added 2026-08-30 from the Microsemi datasheet ingest
# ---------------------------------------------------------------------------

def test_strip_bullet_glyphs_removes_double_bullet_and_pua_glyph() -> None:
    text = "- \u2022 Ultra-high bandwidth\n- \uf0b7 Stratum 1\n1. \uf0b7  numbered\n  - \u25e6 nested"
    out = _builtin_strip_bullet_glyphs(text)
    assert out == "- Ultra-high bandwidth\n- Stratum 1\n1. numbered\n  - nested"


def test_strip_bullet_glyphs_leaves_ordinary_text_alone() -> None:
    text = "- item with  two spaces inside\n\nplain \u00a9\u2122 text \u2022 with a bullet mid-sentence\n| \u2022 Bounce | cell |"
    assert _builtin_strip_bullet_glyphs(text) == text


def test_normalize_superscripts_rejoins_exponents_and_ordinals() -> None:
    text = "Standard ±1×10 -7\nOCXO ±5x10 -9\nfrequency <1x10 -12 at one day\n(1 st 24 hours)\nOptional 2 nd power supply"
    out = _builtin_normalize_superscripts(text)
    assert out == "Standard ±1×10^-7\nOCXO ±5x10^-9\nfrequency <1x10^-12 at one day\n(1st 24 hours)\nOptional 2nd power supply"


def test_normalize_superscripts_does_not_touch_prose_numbers() -> None:
    text = "10 -7 = 3\nroom 10 St Mary\n5 thousand\nthe 3 rd party"
    out = _builtin_normalize_superscripts(text)
    # "10 -7" without ×10 stays; "St" is capitalised; "thousand" is not an ordinal suffix.
    assert out == "10 -7 = 3\nroom 10 St Mary\n5 thousand\nthe 3rd party"


def test_dedupe_table_spans_blanks_spanned_header_and_footnote_cells() -> None:
    text = (
        "|                 | Input BNCs   | Input BNCs   | Output BNCs   | Output BNCs   |\n"
        "|-----------------|--------------|--------------|---------------|---------------|\n"
        "| Standard        | IRIG B       | 10 MHz       | off           | off           |\n"
        "| Included        | Included     | Included     | Included      | Included      |\n"
        "| After one month of continuous operation | After one month of continuous operation |\n"
    )
    out = _builtin_dedupe_table_spans(text)
    lines = out.split("\n")
    # Header row: any adjacent repeat is a span, regardless of length.
    assert lines[0] == "|                 | Input BNCs   |              | Output BNCs   |               |"
    assert lines[1] == "|-----------------|--------------|--------------|---------------|---------------|"
    # Body rows: short repeated values are real data and must survive.
    assert lines[2] == "| Standard        | IRIG B       | 10 MHz       | off           | off           |"
    assert lines[3] == "| Included        | Included     | Included     | Included      | Included      |"
    # ...but a long repeated cell (a spanned footnote) is a span.
    assert lines[4] == "| After one month of continuous operation |" + " " * 41 + "|"


def test_dedupe_table_spans_row_widths_are_preserved() -> None:
    row = "| Output BNCs | Output BNCs | Output BNCs |\n|---|---|---|"
    out = _builtin_dedupe_table_spans(row)
    first = out.split("\n")[0]
    assert len(first) == len(row.split("\n")[0])
    assert first == "| Output BNCs |             |             |"


def test_dedupe_table_spans_ignores_non_table_lines_and_escaped_pipes() -> None:
    text = "Repeated words repeated words | not a table\n| a \\| b long enough | a \\| b long enough |"
    assert _builtin_dedupe_table_spans(text) == text


def test_strip_repeated_headings_keeps_first_copy_at_threshold() -> None:
    text = "## SyncServer S600\n\nintro\n\n## SyncServer S600\n\nbody\n\n## Features\n\n## SyncServer S600\n\n## Features"
    out = _builtin_strip_repeated_headings(text, threshold=3)
    assert out.count("## SyncServer S600") == 1
    assert out.startswith("## SyncServer S600")
    # Two occurrences is below the threshold — untouched.
    assert out.count("## Features") == 2


def test_strip_repeated_headings_is_off_by_default_and_on_via_config() -> None:
    import asyncio

    text = "## Title\n\na\n\n## Title\n\nb\n\n## Title\n\nc"
    node = CleanupNode()
    default = asyncio.run(node.clean(markdown=text))
    assert default.cleaned_markdown.count("## Title") == 3
    assert not any("strip_repeated_headings" in t for t in default.transforms_applied)

    enabled = asyncio.run(
        node.clean(markdown=text, config={"builtin_cleanup": {"strip_repeated_headings": True}})
    )
    assert enabled.cleaned_markdown.count("## Title") == 1
    assert "builtin:strip_repeated_headings" in enabled.transforms_applied


def test_new_builtins_are_on_by_default_and_reported() -> None:
    import asyncio

    text = "-  item\n\n±1×10 -7\n\n| Output BNCs long | Output BNCs long |\n|---|---|\n| a | b |"
    result = asyncio.run(CleanupNode().clean(markdown=text))
    for name in ("strip_bullet_glyphs", "normalize_superscripts", "dedupe_table_spans"):
        assert f"builtin:{name}" in result.transforms_applied
    assert "- item" in result.cleaned_markdown
    assert "10^-7" in result.cleaned_markdown
