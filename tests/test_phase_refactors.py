"""Tests for Phase 1–4 refactors: refine guardrails, cleanup builtins,
normalize formatting-only, html_unescape dedup.
"""

from __future__ import annotations

import pytest

from atlas.llm.deterministic import DeterministicProvider
from atlas.pipeline.cleanup import (
    CleanupNode,
    _builtin_fix_ligatures,
    _builtin_html_unescape,
    _builtin_strip_page_numbers,
    _builtin_strip_repetitive_lines,
    _builtin_strip_zero_width,
)
from atlas.pipeline.refine import (
    REFINE_SYSTEM_PROMPT,
    RefineNode,
    _DEFAULT_MIN_PRESERVATION_RATIO,
)
from atlas.rag.normalize import normalize_markdown


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _SummarizingProvider(DeterministicProvider):
    """Fake provider that returns a severely truncated output."""

    async def chat(self, *, model: str, messages: list, params: dict | None = None) -> str:
        # Extract original markdown from the user prompt and return ~30% of it.
        user_msg = next((m.content for m in messages if m.role == "user"), "")
        original_start = user_msg.find("Original Document:")
        if original_start == -1:
            return "short"
        body = user_msg[original_start + len("Original Document:") :]
        truncated = body.strip()[: max(10, int(len(body) * 0.2))]
        return truncated


class _ExpandingProvider(DeterministicProvider):
    """Fake provider that returns output >= input length."""

    async def chat(self, *, model: str, messages: list, params: dict | None = None) -> str:
        user_msg = next((m.content for m in messages if m.role == "user"), "")
        original_start = user_msg.find("Original Document:")
        if original_start == -1:
            return user_msg
        body = user_msg[original_start + len("Original Document:") :]
        end_marker = body.find("Improved Document:")
        if end_marker != -1:
            body = body[:end_marker]
        return body.strip() + "\n\n<!-- fixed formatting -->"


def _refine_node(
    provider=None, max_retries: int = 2, min_ratio: float = _DEFAULT_MIN_PRESERVATION_RATIO
) -> RefineNode:
    return RefineNode(
        provider=provider or DeterministicProvider(),
        model_name="test-refine",
        model_params={},
        max_retries=max_retries,
        min_preservation_ratio=min_ratio,
    )


# ===================================================================
# Phase 1 — Refine length-preservation guardrail
# ===================================================================


class TestRefinePreservationGuardrail:
    """Verify the min_preservation_ratio guardrail in RefineNode."""

    def test_default_preservation_ratio(self) -> None:
        assert _DEFAULT_MIN_PRESERVATION_RATIO == 0.6

    def test_custom_preservation_ratio(self) -> None:
        node = _refine_node(min_ratio=0.8)
        assert node.min_preservation_ratio == 0.8

    @pytest.mark.asyncio
    async def test_short_output_rejected(self) -> None:
        """When LLM summarizes too aggressively the original text is kept."""
        node = _refine_node(provider=_SummarizingProvider(), min_ratio=0.6)
        original = "# Technical Report\n\n" + "Detailed content. " * 100
        result = await node.refine_document(markdown=original, judge_score=3, retry_count=0)
        assert result.success is False
        assert result.refined_markdown == original
        assert any("output_too_short" in imp for imp in result.improvements_made)

    @pytest.mark.asyncio
    async def test_acceptable_output_passes(self) -> None:
        """When LLM output meets the ratio threshold refinement succeeds."""
        node = _refine_node(provider=_ExpandingProvider(), min_ratio=0.6)
        original = "# Technical Report\n\n" + "Detailed content. " * 100
        result = await node.refine_document(markdown=original, judge_score=3, retry_count=0)
        assert result.success is True

    def test_refine_version_is_v2(self) -> None:
        node = _refine_node()
        assert ":v2" in node.refine_version

    def test_system_prompt_forbids_summarisation(self) -> None:
        assert "MUST NOT" in REFINE_SYSTEM_PROMPT
        assert "summarise" in REFINE_SYSTEM_PROMPT.lower() or "condense" in REFINE_SYSTEM_PROMPT.lower()

    def test_system_prompt_not_in_user_prompt(self) -> None:
        """_build_prompt must NOT embed the system prompt (double-send fix)."""
        node = _refine_node()
        prompt = node._build_prompt("some md", 3)
        assert REFINE_SYSTEM_PROMPT not in prompt
        assert "document refinement assistant" not in prompt.lower()


# ===================================================================
# Phase 2 — New builtin cleanup toggles
# ===================================================================


class TestBuiltinStripPageNumbers:
    def test_simple_page_number(self) -> None:
        text = "content\n3\nmore content"
        result = _builtin_strip_page_numbers(text)
        assert "\n3\n" not in result

    def test_page_n_format(self) -> None:
        text = "content\nPage 5\nmore content"
        result = _builtin_strip_page_numbers(text)
        assert "Page 5" not in result

    def test_fraction_format(self) -> None:
        text = "content\n2 / 10\nmore content"
        result = _builtin_strip_page_numbers(text)
        assert "2 / 10" not in result

    def test_of_format(self) -> None:
        text = "content\n3 of 10\nmore content"
        result = _builtin_strip_page_numbers(text)
        assert "3 of 10" not in result

    def test_does_not_remove_body_text(self) -> None:
        text = "In step 3 we configure the system."
        assert _builtin_strip_page_numbers(text) == text

    def test_does_not_remove_heading_with_number(self) -> None:
        text = "## Section 3"
        assert _builtin_strip_page_numbers(text) == text


class TestBuiltinStripRepetitiveLines:
    def test_strips_frequent_short_lines(self) -> None:
        repeated = "CONFIDENTIAL"
        lines = [repeated] * 10 + ["Real content here."]
        text = "\n".join(lines)
        result = _builtin_strip_repetitive_lines(text, threshold=8, max_chars=80)
        assert "CONFIDENTIAL" not in result
        assert "Real content here." in result

    def test_no_strip_below_threshold(self) -> None:
        repeated = "CONFIDENTIAL"
        lines = [repeated] * 5 + ["Real content here."]
        text = "\n".join(lines)
        result = _builtin_strip_repetitive_lines(text, threshold=8, max_chars=80)
        assert "CONFIDENTIAL" in result

    def test_ignores_long_lines(self) -> None:
        long_line = "x" * 100
        lines = [long_line] * 10 + ["Real content."]
        text = "\n".join(lines)
        result = _builtin_strip_repetitive_lines(text, threshold=8, max_chars=80)
        assert long_line in result

    def test_preserves_empty_lines(self) -> None:
        text = "\n\n\nReal content here.\n\n"
        result = _builtin_strip_repetitive_lines(text, threshold=8, max_chars=80)
        assert "Real content here." in result

    def test_custom_threshold_and_max_chars(self) -> None:
        repeated = "SHORT"
        lines = [repeated] * 4 + ["Content."]
        text = "\n".join(lines)
        result = _builtin_strip_repetitive_lines(text, threshold=4, max_chars=10)
        assert "SHORT" not in result
        assert "Content." in result


class TestCleanupNodeBuiltinToggles:
    @pytest.mark.asyncio
    async def test_page_numbers_stripped_by_default(self) -> None:
        node = CleanupNode()
        md = "# Title\n\nContent here.\n\n3\n\nMore content.\n\nPage 5\n"
        result = await node.clean(markdown=md, config={})
        assert "\n3\n" not in result.cleaned_markdown
        assert "Page 5" not in result.cleaned_markdown
        assert "Content here." in result.cleaned_markdown
        assert "builtin:strip_page_numbers" in result.transforms_applied

    @pytest.mark.asyncio
    async def test_repetitive_lines_off_by_default(self) -> None:
        node = CleanupNode()
        repeated = "FOOTER"
        md = "\n".join([repeated] * 10 + ["Real content."])
        result = await node.clean(markdown=md, config={})
        assert "FOOTER" in result.cleaned_markdown

    @pytest.mark.asyncio
    async def test_repetitive_lines_on_when_enabled(self) -> None:
        node = CleanupNode()
        repeated = "FOOTER"
        md = "\n".join([repeated] * 10 + ["Real content."])
        cfg = {"builtin_cleanup": {"strip_repetitive_lines": True}}
        result = await node.clean(markdown=md, config=cfg)
        assert "FOOTER" not in result.cleaned_markdown
        assert "Real content." in result.cleaned_markdown
        assert "builtin:strip_repetitive_lines" in result.transforms_applied

    @pytest.mark.asyncio
    async def test_page_numbers_disabled_when_toggled(self) -> None:
        node = CleanupNode()
        md = "Content\n3\nMore"
        cfg = {"builtin_cleanup": {"strip_page_numbers": False}}
        result = await node.clean(markdown=md, config=cfg)
        assert "3" in result.cleaned_markdown

    @pytest.mark.asyncio
    async def test_custom_repetitive_threshold(self) -> None:
        node = CleanupNode()
        repeated = "WATERMARK"
        md = "\n".join([repeated] * 5 + ["Real content."])
        cfg = {
            "builtin_cleanup": {
                "strip_repetitive_lines": True,
                "repetitive_line_threshold": 5,
            }
        }
        result = await node.clean(markdown=md, config=cfg)
        assert "WATERMARK" not in result.cleaned_markdown


# ===================================================================
# Phase 2 — Normalize is formatting-only
# ===================================================================


class TestNormalizeFormattingOnly:
    def test_collapses_excessive_blank_lines(self) -> None:
        text = "A\n\n\n\n\nB"
        result = normalize_markdown(text)
        assert result.count("\n\n\n") == 0
        assert "A" in result
        assert "B" in result

    def test_heading_blank_line_enforcement(self) -> None:
        text = "# Title\nParagraph text."
        result = normalize_markdown(text)
        assert "\n\n" in result
        assert "# Title" in result
        assert "Paragraph text." in result

    def test_list_numbering_normalised(self) -> None:
        text = "1) First item\n2) Second item"
        result = normalize_markdown(text)
        assert "1. First item" in result
        assert "2. Second item" in result

    def test_no_content_removal(self) -> None:
        """Normalize must never remove any content lines."""
        lines = [
            "# Heading",
            "",
            "Some paragraph.",
            "",
            "<!-- image -->",
            "<!-- image -->",
            "<!-- image -->",
            "",
            "Another paragraph.",
            "",
            "Short line",
            "Short line",
            "Short line",
            "Short line",
            "Short line",
        ]
        text = "\n".join(lines)
        result = normalize_markdown(text)
        assert "<!-- image -->" in result
        assert result.count("Short line") == 5
        assert "Some paragraph." in result
        assert "Another paragraph." in result

    def test_html_comments_preserved(self) -> None:
        """Previously strip_noise_markdown removed these; normalize must keep them."""
        text = "Content\n\n<!-- image -->\n\nMore content"
        result = normalize_markdown(text)
        assert "<!-- image -->" in result

    def test_page_numbers_preserved(self) -> None:
        """Page number stripping is now in cleanup, not normalize."""
        text = "Content\n\n3\n\nMore content"
        result = normalize_markdown(text)
        assert "\n3\n" in result

    def test_empty_input(self) -> None:
        assert normalize_markdown("") == ""

    def test_whitespace_only_input(self) -> None:
        result = normalize_markdown("   \n\n  \n")
        assert result.strip() == ""


# ===================================================================
# Phase 4 — html_unescape dedup
# ===================================================================


class TestHtmlUnescapeDedup:
    def test_cleanup_rules_delegates_to_builtin(self) -> None:
        """cleanup_rules._step_html_unescape should use cleanup._builtin_html_unescape."""
        from atlas.pipeline.cleanup_rules import _step_html_unescape

        text = "Hello &amp; world &lt;tag&gt;"
        result, count = _step_html_unescape(text, {})
        expected = _builtin_html_unescape(text)
        assert result == expected
        assert result == "Hello & world <tag>"
        assert count > 0

    def test_idempotent(self) -> None:
        """Running html_unescape twice produces same result as once."""
        text = "A &amp; B &lt;C&gt; &#8212; &#x2019;"
        once = _builtin_html_unescape(text)
        twice = _builtin_html_unescape(once)
        assert once == twice

    def test_no_entities_zero_count(self) -> None:
        from atlas.pipeline.cleanup_rules import _step_html_unescape

        text = "No entities here"
        result, count = _step_html_unescape(text, {})
        assert result == text
        assert count == 0


# ===================================================================
# Existing builtin tests (ligatures, zero-width)
# ===================================================================


class TestBuiltinLigatures:
    def test_fi_ligature(self) -> None:
        assert _builtin_fix_ligatures("ﬁne") == "fine"

    def test_fl_ligature(self) -> None:
        assert _builtin_fix_ligatures("ﬂow") == "flow"

    def test_ffi_ligature(self) -> None:
        assert _builtin_fix_ligatures("oﬃce") == "office"


class TestBuiltinZeroWidth:
    def test_strips_zero_width_space(self) -> None:
        assert _builtin_strip_zero_width("he\u200bllo") == "hello"

    def test_strips_bom(self) -> None:
        assert _builtin_strip_zero_width("\ufeffhello") == "hello"

    def test_strips_soft_hyphen(self) -> None:
        assert _builtin_strip_zero_width("hy\u00adphen") == "hyphen"
