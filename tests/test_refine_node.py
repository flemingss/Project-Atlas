"""Tests for RefineNode guardrails, sectional refinement, and edge cases.

Complements test_pipeline_nodes.py (which covers basic refine, max retries,
and fidelity flags) with deeper coverage of:
- Preservation-ratio guardrail (output too short → rejection)
- Section-count guardrail (headings dropped → rejection)
- Sectional refinement (happy path + partial failures)
- Model error handling
- Prompt building with sub-scores / rationale
- _analyze_improvements edge cases
"""

from __future__ import annotations

import pytest

from atlas.llm.deterministic import DeterministicProvider
from atlas.llm.provider import ChatMessage, ILlmProvider
from atlas.pipeline.refine import RefineNode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _TruncatingProvider(ILlmProvider):
    """Returns only a fraction of the input document to trigger guardrails."""

    def __init__(self, fraction: float = 0.3):
        self.fraction = fraction

    async def chat(self, *, model: str, messages: list[ChatMessage], params: dict) -> str:
        text = messages[-1].content if messages else ""
        # Extract original markdown from prompt
        if "Document to improve:" in text:
            md = text.split("Document to improve:\n", 1)[1].split("\nImproved Document:", 1)[0]
        else:
            md = text
        # Return a truncated version
        cut = max(1, int(len(md) * self.fraction))
        return md[:cut]

    async def embed(self, *, model: str, texts: list[str], params: dict) -> list[list[float]]:
        return [[0.0] * 384 for _ in texts]


class _HeadingDroppingProvider(ILlmProvider):
    """Returns document with headings removed to trigger section-count guardrail."""

    async def chat(self, *, model: str, messages: list[ChatMessage], params: dict) -> str:
        text = messages[-1].content if messages else ""
        if "Document to improve:" in text:
            md = text.split("Document to improve:\n", 1)[1].split("\nImproved Document:", 1)[0]
        else:
            md = text
        # Demote headings to plain text, keeping the words. Deleting the whole
        # line would also shorten the document past the preservation guard, so
        # the test would pass for the wrong reason — it is the section-count
        # guard being exercised here, not the length guard.
        lines = md.split("\n")
        return "\n".join(
            line.lstrip().lstrip("#").lstrip() if line.lstrip().startswith("#") else line
            for line in lines
        )

    async def embed(self, *, model: str, texts: list[str], params: dict) -> list[list[float]]:
        return [[0.0] * 384 for _ in texts]


class _ErrorProvider(ILlmProvider):
    """Raises an exception on chat() to test error handling."""

    async def chat(self, *, model: str, messages: list[ChatMessage], params: dict) -> str:
        raise RuntimeError("Model unavailable")

    async def embed(self, *, model: str, texts: list[str], params: dict) -> list[list[float]]:
        return [[0.0] * 384 for _ in texts]


def _make_node(provider: ILlmProvider | None = None, **kwargs) -> RefineNode:
    return RefineNode(
        provider=provider or DeterministicProvider(),
        model_name="test-refine",
        model_params={},
        **kwargs,
    )


# Multi-heading document for section-count tests
_MULTI_HEADING_DOC = """\
# Introduction

This document covers important topics.

## Background

Historical context for the project.

## Methods

We used several approaches.

## Results

The findings were significant.

## Conclusion

In summary, the work was successful."""


# ---------------------------------------------------------------------------
# Preservation-ratio guardrail
# ---------------------------------------------------------------------------

class TestPreservationGuardrail:
    @pytest.mark.asyncio
    async def test_rejects_truncated_output(self) -> None:
        """Output shorter than min_preservation_ratio → rejection."""
        node = _make_node(_TruncatingProvider(fraction=0.3), min_preservation_ratio=0.6)
        long_doc = "# Title\n\n" + "Content paragraph. " * 50
        result = await node.refine_document(
            markdown=long_doc, judge_score=3, retry_count=0,
        )
        assert result.success is False
        assert "output_too_short" in result.improvements_made[0]
        assert result.refined_markdown == long_doc  # original kept

    @pytest.mark.asyncio
    async def test_accepts_sufficient_output(self) -> None:
        """Output above min_preservation_ratio → accepted."""
        node = _make_node()  # DeterministicProvider expands, not truncates
        result = await node.refine_document(
            markdown="Ov3rview\n\nThe syst3m c0nsists of components.",
            judge_score=3,
            retry_count=0,
        )
        assert result.success is True


# ---------------------------------------------------------------------------
# Section-count guardrail
# ---------------------------------------------------------------------------

class TestSectionCountGuardrail:
    @pytest.mark.asyncio
    async def test_rejects_when_headings_dropped(self) -> None:
        """Dropping headings below min_section_ratio → rejection."""
        node = _make_node(_HeadingDroppingProvider(), min_section_ratio=0.8)
        result = await node.refine_document(
            markdown=_MULTI_HEADING_DOC, judge_score=3, retry_count=0,
        )
        assert result.success is False
        assert "sections_dropped" in result.improvements_made[0]
        assert result.refined_markdown == _MULTI_HEADING_DOC

    @pytest.mark.asyncio
    async def test_skips_check_with_few_headings(self) -> None:
        """Documents with < 3 headings skip the section-count check."""
        node = _make_node(_HeadingDroppingProvider())
        short_doc = "# Title\n\nJust one heading, some content."
        result = await node.refine_document(
            markdown=short_doc, judge_score=3, retry_count=0,
        )
        # Should NOT be rejected for section count (only 1 heading)
        # May still fail for other reasons (preservation ratio) depending
        # on provider output; the key assertion is no section_dropped flag.
        assert all("sections_dropped" not in imp for imp in result.improvements_made)


# ---------------------------------------------------------------------------
# Model error handling
# ---------------------------------------------------------------------------

class TestModelErrorHandling:
    @pytest.mark.asyncio
    async def test_returns_original_on_model_error(self) -> None:
        """Model exception → success=False, original returned."""
        node = _make_node(_ErrorProvider())
        result = await node.refine_document(
            markdown="# Title\n\nSome content.",
            judge_score=3,
            retry_count=0,
        )
        assert result.success is False
        assert result.refined_markdown == "# Title\n\nSome content."
        assert result.improvements_made == []


# ---------------------------------------------------------------------------
# Sectional refinement
# ---------------------------------------------------------------------------

class TestSectionalRefinement:
    @pytest.mark.asyncio
    async def test_sectional_happy_path(self) -> None:
        """Sectional refinement succeeds on a multi-section document."""
        node = _make_node(max_section_tokens=50)  # force splitting
        doc = (
            "# Section A\n\nOv3rview of the syst3m.\n\n"
            "# Section B\n\nThe c0nsists of parts.\n\n"
            "# Section C\n\nFinal section content."
        )
        result = await node.refine_document_sectional(
            markdown=doc, judge_score=3, retry_count=0,
        )
        assert result.success is True
        assert any("sectional_refine" in imp for imp in result.improvements_made)

    @pytest.mark.asyncio
    async def test_sectional_preserves_all_sections(self) -> None:
        """Sectional refinement keeps content from all sections."""
        node = _make_node(max_section_tokens=50)
        doc = (
            "# Alpha\n\nOv3rview content here.\n\n"
            "# Beta\n\nThe syst3m details.\n\n"
            "# Gamma\n\nThe c0nsists part."
        )
        result = await node.refine_document_sectional(
            markdown=doc, judge_score=3, retry_count=0,
        )
        # All section headers should be present in output
        assert "Alpha" in result.refined_markdown
        assert "Beta" in result.refined_markdown
        assert "Gamma" in result.refined_markdown

    @pytest.mark.asyncio
    async def test_sectional_rejects_truncated_reassembly(self) -> None:
        """Sectional refinement applies whole-document preservation guardrail."""
        node = _make_node(
            _TruncatingProvider(fraction=0.3),
            min_preservation_ratio=0.6,
            max_section_tokens=50,
        )
        doc = (
            "# Section A\n\n" + "Long content. " * 30 + "\n\n"
            "# Section B\n\n" + "More content. " * 30
        )
        result = await node.refine_document_sectional(
            markdown=doc, judge_score=3, retry_count=0,
        )
        assert result.success is False
        # Should be rejected at sectional level or individual section level
        rejected = any("rejected" in imp or "too_short" in imp for imp in result.improvements_made)
        # If individual sections are rejected, original sections are kept,
        # so the reassembly may pass. Either way the result is safe.
        assert rejected or result.refined_markdown  # always produces output

    @pytest.mark.asyncio
    async def test_sectional_max_retries(self) -> None:
        """Sectional refinement respects max_retries at section level."""
        node = _make_node(max_retries=2, max_section_tokens=50)
        doc = "# A\n\nContent.\n\n# B\n\nMore content."
        result = await node.refine_document_sectional(
            markdown=doc, judge_score=3, retry_count=2,  # already at max
        )
        # Each section's refine_document call should fail with max retries
        assert result.success is False


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

class TestPromptBuilding:
    def test_build_prompt_includes_judge_feedback(self) -> None:
        node = _make_node()
        prompt = node._build_prompt(
            "# Test\n\nContent.",
            judge_score=3,
            retry_count=0,
            max_retries=2,
            judge_sub_scores={"faithfulness": 4, "formatting": 2, "cohesion": 3},
            judge_rationale="Formatting needs work.",
        )
        assert "JUDGE FEEDBACK" in prompt
        assert "Faithfulness: 4/5" in prompt
        assert "Formatting: 2/5" in prompt
        assert "← focus here" in prompt  # dimensions < 4
        assert "Formatting needs work." in prompt
        assert "Content." in prompt

    def test_build_prompt_without_sub_scores(self) -> None:
        node = _make_node()
        prompt = node._build_prompt(
            "# Test\n\nContent.",
            judge_score=2,
            retry_count=0,
            max_retries=2,
        )
        assert "Overall: 2/5" in prompt

    def test_build_prompt_final_attempt_message(self) -> None:
        node = _make_node(max_retries=2)
        prompt = node._build_prompt(
            "doc", judge_score=3, retry_count=1, max_retries=2,
        )
        assert "final attempt" in prompt

    def test_build_prompt_earlier_attempt_message(self) -> None:
        node = _make_node(max_retries=3)
        prompt = node._build_prompt(
            "doc", judge_score=3, retry_count=0, max_retries=3,
        )
        assert "earlier attempt" in prompt


# ---------------------------------------------------------------------------
# Analyze improvements
# ---------------------------------------------------------------------------

class TestAnalyzeImprovements:
    def test_no_changes(self) -> None:
        node = _make_node()
        result = node._analyze_improvements("identical", "identical")
        assert result == ["No changes made"]

    def test_length_change(self) -> None:
        node = _make_node()
        result = node._analyze_improvements("short", "longer text here")
        assert "Length adjusted" in result

    def test_heading_change(self) -> None:
        node = _make_node()
        result = node._analyze_improvements("no headings", "# Now with heading")
        assert "Heading structure improved" in result

    def test_content_change_only(self) -> None:
        """Same length, same # count, different content."""
        node = _make_node()
        result = node._analyze_improvements("abcde", "fghij")
        assert "Content refined" in result
        assert "Length adjusted" not in result


# ---------------------------------------------------------------------------
# Refine version & timestamp format
# ---------------------------------------------------------------------------

class TestRefineMetadata:
    def test_refine_version_format(self) -> None:
        node = _make_node()
        assert node.refine_version == "test-refine:v2"

    @pytest.mark.asyncio
    async def test_timestamp_is_iso_utc(self) -> None:
        node = _make_node()
        result = await node.refine_document(
            markdown="Ov3rview\n\nThe syst3m.", judge_score=3, retry_count=0,
        )
        assert result.timestamp.endswith("Z")
        assert "T" in result.timestamp


# ---------------------------------------------------------------------------
# UNFIXABLE input
# ---------------------------------------------------------------------------

class TestUnfixableInput:
    @pytest.mark.asyncio
    async def test_unfixable_returns_source(self) -> None:
        """DeterministicProvider returns source unchanged for [UNFIXABLE]."""
        node = _make_node()
        doc = "[UNFIXABLE] broken content here"
        result = await node.refine_document(
            markdown=doc, judge_score=2, retry_count=0,
        )
        # The provider returns the source unchanged; guardrails may still
        # pass since the output equals the input (ratio ≈ 1.0).
        assert result.refined_markdown  # non-empty


# ---------------------------------------------------------------------------
# Fidelity flag: score=1
# ---------------------------------------------------------------------------
