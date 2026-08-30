"""Tests for JudgeNode — parsing robustness, scoring, and edge cases."""

from __future__ import annotations

import pytest

from atlas.llm.deterministic import DeterministicProvider
from atlas.llm.provider import ChatMessage, ILlmProvider
from atlas.pipeline.judge import (
    JUDGE_DIMENSIONS,
    JUDGE_SYSTEM_PROMPT,
    JUDGE_SYSTEM_PROMPT_WITH_REFERENCE,
    JudgeNode,
    _prompt_hash,
)


def _judge_node() -> JudgeNode:
    return JudgeNode(
        provider=DeterministicProvider(),
        model_name="det-judge",
        model_params={},
    )


class _CapturingProvider(ILlmProvider):
    """Provider that records the messages it receives and delegates to
    DeterministicProvider for the response."""

    def __init__(self) -> None:
        self.messages: list[ChatMessage] = []

    async def chat(
        self, *, model: str, messages: list[ChatMessage], params: dict
    ) -> str:
        self.messages = list(messages)
        return await DeterministicProvider().chat(
            model=model, messages=messages, params=params
        )

    async def embed(
        self, *, model: str, texts: list[str], params: dict
    ) -> list[list[float]]:
        return await DeterministicProvider().embed(model=model, texts=texts, params=params)


# ---------------------------------------------------------------------------
# _extract_int — handles markdown formatting in score values
# ---------------------------------------------------------------------------

class TestExtractInt:
    def test_plain_number(self) -> None:
        assert JudgeNode._extract_int("5") == 5

    def test_with_whitespace(self) -> None:
        assert JudgeNode._extract_int("  4  ") == 4

    def test_bold_markdown(self) -> None:
        assert JudgeNode._extract_int("**5**") == 5

    def test_italic_markdown(self) -> None:
        assert JudgeNode._extract_int("*4*") == 4

    def test_backtick_wrapped(self) -> None:
        assert JudgeNode._extract_int("`3`") == 3

    def test_bracketed(self) -> None:
        assert JudgeNode._extract_int("[5]") == 5

    def test_trailing_period(self) -> None:
        assert JudgeNode._extract_int("4.") == 4

    def test_trailing_commentary(self) -> None:
        assert JudgeNode._extract_int("5 (excellent)") == 5

    def test_non_numeric(self) -> None:
        assert JudgeNode._extract_int("excellent") is None

    def test_empty(self) -> None:
        assert JudgeNode._extract_int("") is None


# ---------------------------------------------------------------------------
# _parse_response — standard and edge-case parsing
# ---------------------------------------------------------------------------

class TestParseResponse:
    def test_standard_format(self) -> None:
        response = (
            "FAITHFULNESS: 5\n"
            "FORMATTING: 4\n"
            "COHESION: 5\n"
            "HALLUCINATION_RISK: 5\n"
            "RATIONALE: All good."
        )
        node = _judge_node()
        scores, rationale = node._parse_response(response)
        assert scores == {"faithfulness": 5, "formatting": 4, "cohesion": 5, "hallucination_risk": 5}
        assert rationale == "All good."

    def test_bold_scores(self) -> None:
        response = (
            "FAITHFULNESS: **5**\n"
            "FORMATTING: **4**\n"
            "COHESION: **3**\n"
            "HALLUCINATION_RISK: **5**\n"
            "RATIONALE: Some issues."
        )
        node = _judge_node()
        scores, rationale = node._parse_response(response)
        assert scores["faithfulness"] == 5
        assert scores["formatting"] == 4
        assert scores["cohesion"] == 3
        assert scores["hallucination_risk"] == 5

    def test_parenthesised_dimension_names(self) -> None:
        response = (
            "Score (Faithfulness): 5\n"
            "Score (Formatting): 4\n"
            "Score (Cohesion): 3\n"
            "Score (Hallucination_Risk): 5\n"
            "RATIONALE: Mixed."
        )
        node = _judge_node()
        scores, rationale = node._parse_response(response)
        assert scores["faithfulness"] == 5
        assert scores["formatting"] == 4
        assert scores["cohesion"] == 3
        assert scores["hallucination_risk"] == 5

    def test_score_clamping_high(self) -> None:
        response = "FAITHFULNESS: 10\nFORMATTING: 4\nCOHESION: 4\nHALLUCINATION_RISK: 4\nRATIONALE: ok"
        node = _judge_node()
        scores, _ = node._parse_response(response)
        assert scores["faithfulness"] == 5

    def test_score_clamping_low(self) -> None:
        response = "FAITHFULNESS: -1\nFORMATTING: 4\nCOHESION: 4\nHALLUCINATION_RISK: 4\nRATIONALE: ok"
        node = _judge_node()
        scores, _ = node._parse_response(response)
        assert scores["faithfulness"] == 1

    def test_legacy_single_score_fallback(self) -> None:
        response = "SCORE: 4\nRATIONALE: Legacy format."
        node = _judge_node()
        scores, rationale = node._parse_response(response)
        for d in JUDGE_DIMENSIONS:
            assert scores[d] == 4
        assert rationale == "Legacy format."

    def test_unparseable_falls_back_to_3(self) -> None:
        response = "This is not a valid judge response at all."
        node = _judge_node()
        scores, rationale = node._parse_response(response)
        for d in JUDGE_DIMENSIONS:
            assert scores[d] == 3

    def test_empty_response(self) -> None:
        node = _judge_node()
        scores, _ = node._parse_response("")
        for d in JUDGE_DIMENSIONS:
            assert scores[d] == 3

    def test_mixed_case_dimensions(self) -> None:
        response = (
            "faithfulness: 5\n"
            "Formatting: 4\n"
            "COHESION: 5\n"
            "hallucination_risk: 5\n"
            "Rationale: Mixed case."
        )
        node = _judge_node()
        scores, rationale = node._parse_response(response)
        assert scores["faithfulness"] == 5
        assert scores["formatting"] == 4
        assert rationale == "Mixed case."


# ---------------------------------------------------------------------------
# grade_document — end-to-end
# ---------------------------------------------------------------------------

class TestGradeDocument:
    @pytest.mark.asyncio
    async def test_grade_returns_judge_result(self) -> None:
        node = _judge_node()
        result = await node.grade_document(markdown="# Test\n\nSome content.")
        assert result.score >= 1
        assert result.score <= 5
        assert result.judge_version == node.judge_version
        assert result.sub_scores is not None
        assert len(result.sub_scores) == len(JUDGE_DIMENSIONS)

    @pytest.mark.asyncio
    async def test_needs_refinement_below_cutoff(self) -> None:
        node = _judge_node()
        result = await node.grade_document(markdown="# Test\n\nContent.", judge_cutoff=10)
        assert result.needs_refinement is True

    @pytest.mark.asyncio
    async def test_no_refinement_at_or_above_cutoff(self) -> None:
        node = _judge_node()
        result = await node.grade_document(markdown="# Test\n\nContent.", judge_cutoff=1)
        assert result.needs_refinement is False


# ---------------------------------------------------------------------------
# grade_document — reference-passing (post-refine judge pass)
# ---------------------------------------------------------------------------

class TestGradeDocumentWithReference:
    @pytest.mark.asyncio
    async def test_reference_block_included_in_prompt(self) -> None:
        provider = _CapturingProvider()
        node = JudgeNode(
            provider=provider,
            model_name="det-judge",
            model_params={},
            pass_reference_on_refine=True,
        )
        reference = "# Original\n\nOv3rview of the syst3m."
        await node.grade_document(
            markdown="# Refined\n\nOverview of the system.",
            reference_markdown=reference,
        )
        user_prompt = provider.messages[-1].content
        assert isinstance(user_prompt, str)
        assert "Reference (pre-refine original" in user_prompt
        assert reference in user_prompt
        # The literal marker the DeterministicProvider stub keys on must survive.
        assert "Now grade this document:" in user_prompt
        # Reference block must come BEFORE the document-to-grade marker.
        assert user_prompt.index("Reference (pre-refine original") < user_prompt.index(
            "Now grade this document:"
        )
        # The reference-aware system prompt variant must be selected.
        system_prompt = provider.messages[0].content
        assert system_prompt == JUDGE_SYSTEM_PROMPT_WITH_REFERENCE

    @pytest.mark.asyncio
    async def test_no_reference_no_reference_block(self) -> None:
        provider = _CapturingProvider()
        node = JudgeNode(provider=provider, model_name="det-judge", model_params={})
        await node.grade_document(markdown="# Test\n\nContent.")
        user_prompt = provider.messages[-1].content
        assert isinstance(user_prompt, str)
        assert "Reference (pre-refine original" not in user_prompt
        assert "Now grade this document:" in user_prompt
        assert provider.messages[0].content == JUDGE_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_reference_none_keeps_default_system_prompt(self) -> None:
        provider = _CapturingProvider()
        node = JudgeNode(provider=provider, model_name="det-judge", model_params={})
        await node.grade_document(markdown="# Test\n\nContent.", reference_markdown=None)
        assert provider.messages[0].content == JUDGE_SYSTEM_PROMPT
        user_prompt = provider.messages[-1].content
        assert isinstance(user_prompt, str)
        assert "Reference (pre-refine original" not in user_prompt

    def test_prompt_variants_produce_distinct_hashes(self) -> None:
        assert JUDGE_SYSTEM_PROMPT != JUDGE_SYSTEM_PROMPT_WITH_REFERENCE
        assert _prompt_hash() != _prompt_hash(JUDGE_SYSTEM_PROMPT_WITH_REFERENCE)

    @pytest.mark.asyncio
    async def test_reference_grade_reports_reference_aware_version(self) -> None:
        node = _judge_node()
        with_ref = await node.grade_document(
            markdown="# Refined\n\nContent.",
            reference_markdown="# Original\n\nContent.",
        )
        without_ref = await node.grade_document(markdown="# Refined\n\nContent.")
        assert with_ref.judge_version != without_ref.judge_version
        assert without_ref.judge_version == node.judge_version
        assert _prompt_hash(JUDGE_SYSTEM_PROMPT_WITH_REFERENCE) in with_ref.judge_version

    @pytest.mark.asyncio
    async def test_default_grade_document_unchanged(self) -> None:
        node = _judge_node()
        result = await node.grade_document(markdown="# Test\n\nSome content.")
        assert result.score >= 1
        assert result.score <= 5
        assert result.judge_version == node.judge_version
        assert set(result.sub_scores.keys()) == set(JUDGE_DIMENSIONS)
