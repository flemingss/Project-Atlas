"""Tests for JudgeNode — parsing robustness, scoring, and edge cases."""

from __future__ import annotations

import pytest

from atlas.llm.deterministic import DeterministicProvider
from atlas.pipeline.judge import JUDGE_DIMENSIONS, JudgeNode


def _judge_node() -> JudgeNode:
    return JudgeNode(
        provider=DeterministicProvider(),
        model_name="det-judge",
        model_params={},
    )


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
