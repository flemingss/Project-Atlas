"""Tests for atlas.pipeline.tokens — token estimation and section splitting."""

from __future__ import annotations

from atlas.pipeline.tokens import (
    count_headings,
    estimate_tokens,
    fits_in_context,
    split_into_sections,
)


class TestEstimateTokens:
    def test_empty_string(self) -> None:
        assert estimate_tokens("") == 1  # min 1

    def test_short_text(self) -> None:
        result = estimate_tokens("Hello world")
        assert result >= 1

    def test_scales_with_length(self) -> None:
        short = estimate_tokens("abc")
        long = estimate_tokens("a" * 1000)
        assert long > short

    def test_returns_int(self) -> None:
        assert isinstance(estimate_tokens("some text"), int)


class TestCountHeadings:
    def test_no_headings(self) -> None:
        assert count_headings("Just some text.\nAnother line.") == 0

    def test_multiple_levels(self) -> None:
        text = "# H1\n## H2\n### H3\ntext\n#### H4"
        assert count_headings(text) == 4

    def test_ignores_non_headings(self) -> None:
        text = "Not a #heading\n#also not (no space)\n# Real heading"
        assert count_headings(text) == 1


class TestFitsInContext:
    def test_short_text_fits(self) -> None:
        assert fits_in_context("Hello", 10000) is True

    def test_huge_text_does_not_fit(self) -> None:
        huge = "word " * 50000
        assert fits_in_context(huge, 1000) is False

    def test_prompt_overhead_matters(self) -> None:
        text = "x" * 3700  # ~1000 tokens
        # With overhead 500 and ratio 1.15: total ~2650
        assert fits_in_context(text, 3000) is True
        assert fits_in_context(text, 2000) is False


class TestSplitIntoSections:
    def test_short_doc_single_section(self) -> None:
        text = "# Title\n\nShort document."
        sections = split_into_sections(text, max_section_tokens=10000)
        assert len(sections) == 1

    def test_splits_on_headings(self) -> None:
        text = "# Section 1\n\nContent 1.\n\n# Section 2\n\nContent 2."
        sections = split_into_sections(text, max_section_tokens=10000)
        assert len(sections) == 2
        assert "Section 1" in sections[0]
        assert "Section 2" in sections[1]

    def test_preamble_preserved(self) -> None:
        text = "Some preamble text.\n\n# First Section\n\nContent."
        sections = split_into_sections(text, max_section_tokens=10000)
        assert len(sections) >= 2
        assert "preamble" in sections[0]

    def test_empty_returns_original(self) -> None:
        sections = split_into_sections("", max_section_tokens=10000)
        assert len(sections) == 1

    def test_secondary_split_on_h3(self) -> None:
        # Create a section that's too long for primary but splittable on ###
        big_content = "word " * 5000
        text = f"# Big Section\n\n{big_content}\n\n### Sub A\n\nContent A.\n\n### Sub B\n\nContent B."
        sections = split_into_sections(text, max_section_tokens=500)
        assert len(sections) >= 2
