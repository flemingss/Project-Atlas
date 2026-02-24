"""Tests for judge, refine, and metadata pipeline nodes."""

from __future__ import annotations

import pytest

from atlas.llm.deterministic import DeterministicProvider
from atlas.pipeline.judge import JudgeNode, _prompt_hash
from atlas.pipeline.metadata import MetadataNode
from atlas.pipeline.refine import RefineNode
from atlas.schemas import FidelityFlag


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _judge_node() -> JudgeNode:
    return JudgeNode(
        provider=DeterministicProvider(),
        model_name="det-judge",
        model_params={},
    )


def _refine_node(max_retries: int = 2) -> RefineNode:
    return RefineNode(
        provider=DeterministicProvider(),
        model_name="det-refine",
        model_params={},
        max_retries=max_retries,
    )


def _metadata_node(with_tier2: bool = False) -> MetadataNode:
    provider = DeterministicProvider()
    return MetadataNode(
        tier1_provider=provider,
        tier1_model="det-meta1",
        tier2_provider=provider if with_tier2 else None,
        tier2_model="det-meta2" if with_tier2 else None,
        tier2_cap_per_doc=5,
    )


# ---------------------------------------------------------------------------
# Judge node tests
# ---------------------------------------------------------------------------

def test_judge_version_includes_prompt_hash() -> None:
    node = _judge_node()
    # judge_version must embed the prompt hash, not just "v1"
    assert "ph-" in node.judge_version
    assert _prompt_hash() in node.judge_version


def test_judge_version_is_deterministic() -> None:
    a = _judge_node()
    b = _judge_node()
    assert a.judge_version == b.judge_version


def test_judge_prompt_hash_is_stable() -> None:
    h1 = _prompt_hash()
    h2 = _prompt_hash()
    assert h1 == h2
    assert len(h1) == 12


@pytest.mark.asyncio
async def test_judge_grades_good_document() -> None:
    node = _judge_node()
    result = await node.grade_document(markdown="# Technical Report\n\n" + "Content. " * 30)
    assert result.score >= 4
    assert not result.needs_refinement
    assert result.judge_version


@pytest.mark.asyncio
async def test_judge_grades_corrupt_document() -> None:
    node = _judge_node()
    result = await node.grade_document(markdown="[UNFIXABLE]\n\n\uFFFD\uFFFD\uFFFD gibberish")
    assert result.score == 1
    assert result.needs_refinement


@pytest.mark.asyncio
async def test_judge_returns_fallback_on_model_error() -> None:
    """If the LLM returns an unparseable response the fallback score (3) is returned."""
    node = _judge_node()
    # Directly test the parser with garbage input.
    score, rationale = node._parse_response("totally unparseable")
    assert 1 <= score <= 5
    assert isinstance(rationale, str) and rationale


def test_judge_parse_valid_response() -> None:
    node = _judge_node()
    score, rationale = node._parse_response("SCORE: 4\nRATIONALE: Mostly readable.")
    assert score == 4
    assert "readable" in rationale


def test_judge_parse_clamped_on_out_of_range() -> None:
    node = _judge_node()
    score, _ = node._parse_response("SCORE: 99\nRATIONALE: x")
    assert 1 <= score <= 5


# ---------------------------------------------------------------------------
# Refine node tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refine_improves_document() -> None:
    node = _refine_node()
    result = await node.refine_document(
        markdown="Ov3rview\n\nThe syst3m c0nsists of components.",
        judge_score=3,
        retry_count=0,
    )
    assert result.success is True
    assert result.refined_markdown  # non-empty


@pytest.mark.asyncio
async def test_refine_max_retries_returns_original() -> None:
    node = _refine_node(max_retries=2)
    result = await node.refine_document(
        markdown="some content",
        judge_score=2,
        retry_count=2,  # already at max
    )
    assert result.success is False
    assert result.refined_markdown == "some content"


def test_refine_fidelity_flag_needs_review_when_retries_exceeded() -> None:
    node = _refine_node(max_retries=2)
    flag = node.determine_fidelity_flag(judge_score=3, refine_success=True, retry_count=2)
    assert flag == FidelityFlag.NEEDS_REVIEW


def test_refine_fidelity_flag_verified_on_high_score() -> None:
    node = _refine_node()
    flag = node.determine_fidelity_flag(judge_score=5, refine_success=False, retry_count=0)
    assert flag == FidelityFlag.VERIFIED


def test_refine_fidelity_flag_verified_on_score4() -> None:
    node = _refine_node()
    flag = node.determine_fidelity_flag(judge_score=4, refine_success=False, retry_count=1)
    assert flag == FidelityFlag.VERIFIED


def test_refine_fidelity_flag_low_confidence_on_low_score() -> None:
    node = _refine_node()
    flag = node.determine_fidelity_flag(judge_score=2, refine_success=False, retry_count=0)
    assert flag == FidelityFlag.LOW_CONFIDENCE


def test_refine_fidelity_flag_partial_on_borderline_score() -> None:
    node = _refine_node()
    flag = node.determine_fidelity_flag(judge_score=3, refine_success=True, retry_count=1)
    assert flag == FidelityFlag.PARTIAL


def test_refine_fidelity_flag_retries_exceeded_overrides_high_score() -> None:
    """NEEDS_REVIEW takes priority over VERIFIED even if score is high."""
    node = _refine_node(max_retries=2)
    flag = node.determine_fidelity_flag(judge_score=5, refine_success=True, retry_count=2)
    assert flag == FidelityFlag.NEEDS_REVIEW


# ---------------------------------------------------------------------------
# Metadata node tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_metadata_tier1_returns_required_fields() -> None:
    node = _metadata_node()
    result = await node.generate_metadata(content="This is a technical document about AI.")
    assert result.tier == 1
    assert "topic" in result.tags
    assert "keywords" in result.tags
    assert "density" in result.tags
    assert isinstance(result.tags["keywords"], list)


@pytest.mark.asyncio
async def test_metadata_tier2_used_for_borderline_judge_score() -> None:
    node = _metadata_node(with_tier2=True)
    result = await node.generate_metadata(content="AI content.", judge_score=3.5, tier2_count=0)
    assert result.tier == 2


@pytest.mark.asyncio
async def test_metadata_tier2_cap_enforced() -> None:
    node = _metadata_node(with_tier2=True)
    # tier2_count is already at cap (5), should fall back to tier 1
    result = await node.generate_metadata(content="AI content.", judge_score=3.5, tier2_count=5)
    assert result.tier == 1


@pytest.mark.asyncio
async def test_metadata_tier2_fallback_when_not_configured() -> None:
    """When tier 2 is not configured, selecting tier 2 must fall back to tier 1."""
    node = _metadata_node(with_tier2=False)
    result = await node.generate_metadata(content="Technical content.", judge_score=3.0, tier2_count=0)
    assert result.tier == 1


@pytest.mark.asyncio
async def test_metadata_schema_validation_fills_missing_fields() -> None:
    node = _metadata_node()
    # _validate_metadata_schema should fill in missing required fields.
    tags: dict = {}
    filled = node._validate_metadata_schema(tags, tier=1)
    assert "topic" in filled
    assert "keywords" in filled
    assert "density" in filled


@pytest.mark.asyncio
async def test_metadata_schema_validation_tier2_fills_extra_fields() -> None:
    node = _metadata_node()
    tags: dict = {"topic": "AI", "keywords": ["ml"], "density": "technical"}
    filled = node._validate_metadata_schema(tags, tier=2)
    assert "concepts" in filled
    assert "complexity_score" in filled
    assert "domain" in filled


def test_metadata_schema_validation_coerces_keywords_to_list() -> None:
    node = _metadata_node()
    tags = {"topic": "AI", "keywords": "machine learning", "density": "technical"}
    filled = node._validate_metadata_schema(tags, tier=1)
    assert isinstance(filled["keywords"], list)
