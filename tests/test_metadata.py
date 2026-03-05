"""Tests for MetadataNode — tier selection, generation, and schema validation."""

from __future__ import annotations

import pytest

from atlas.llm.deterministic import DeterministicProvider
from atlas.pipeline.metadata import MetadataNode


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
# Tier selection
# ---------------------------------------------------------------------------

class TestTierSelection:
    def test_default_is_tier1(self) -> None:
        node = _metadata_node()
        assert node._select_tier(judge_score=5, tier2_count=0) == 1

    def test_borderline_score_triggers_tier2(self) -> None:
        node = _metadata_node()
        assert node._select_tier(judge_score=3, tier2_count=0) == 2
        assert node._select_tier(judge_score=4, tier2_count=0) == 2

    def test_high_score_stays_tier1(self) -> None:
        node = _metadata_node()
        assert node._select_tier(judge_score=5, tier2_count=0) == 1

    def test_low_score_stays_tier1(self) -> None:
        node = _metadata_node()
        assert node._select_tier(judge_score=2, tier2_count=0) == 1

    def test_cap_enforced(self) -> None:
        node = _metadata_node()
        assert node._select_tier(judge_score=3, tier2_count=5) == 1

    def test_none_score_defaults_tier1(self) -> None:
        node = _metadata_node()
        assert node._select_tier(judge_score=None, tier2_count=0) == 1


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

class TestValidateSchema:
    def test_adds_missing_tier1_fields(self) -> None:
        node = _metadata_node()
        tags = node._validate_metadata_schema({}, tier=1)
        assert "topic" in tags
        assert "keywords" in tags
        assert "density" in tags

    def test_adds_missing_tier2_fields(self) -> None:
        node = _metadata_node()
        tags = node._validate_metadata_schema({}, tier=2)
        assert "concepts" in tags
        assert "complexity_score" in tags
        assert "domain" in tags

    def test_preserves_existing_values(self) -> None:
        node = _metadata_node()
        tags = node._validate_metadata_schema(
            {"topic": "AI", "keywords": ["ml"], "density": "technical"}, tier=1
        )
        assert tags["topic"] == "AI"
        assert tags["keywords"] == ["ml"]

    def test_coerces_string_keywords_to_list(self) -> None:
        node = _metadata_node()
        tags = node._validate_metadata_schema(
            {"topic": "test", "keywords": "a, b, c", "density": "general"}, tier=1
        )
        assert isinstance(tags["keywords"], list)
        assert "a" in tags["keywords"]


# ---------------------------------------------------------------------------
# End-to-end generation
# ---------------------------------------------------------------------------

class TestGenerateMetadata:
    @pytest.mark.asyncio
    async def test_tier1_generation(self) -> None:
        node = _metadata_node()
        result = await node.generate_metadata(content="Some document content.")
        assert result.tier == 1
        assert result.model_used == "det-meta1"
        assert isinstance(result.tags, dict)

    @pytest.mark.asyncio
    async def test_tier2_fallback_when_not_configured(self) -> None:
        node = _metadata_node(with_tier2=False)
        result = await node.generate_metadata(
            content="Content.", judge_score=3, force_tier=2
        )
        # Should fall back to tier 1 internally
        assert result.model_used == "det-meta1"

    @pytest.mark.asyncio
    async def test_tier2_generation(self) -> None:
        node = _metadata_node(with_tier2=True)
        result = await node.generate_metadata(
            content="Technical content.", force_tier=2
        )
        assert result.tier == 2
        assert result.model_used == "det-meta2"

    @pytest.mark.asyncio
    async def test_force_tier_overrides_selection(self) -> None:
        node = _metadata_node(with_tier2=True)
        result = await node.generate_metadata(
            content="Content.", judge_score=5, force_tier=2
        )
        assert result.tier == 2
