"""Metadata generation node for Project Atlas pipeline (HLD section 2: Metadata).

Implements tiered metadata generation with cost-aware routing.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from atlas.diagnostics import ErrorCode, get_diagnostics
from atlas.llm.provider import ILlmProvider
from atlas.llm.provider import ChatMessage
from atlas.schemas import MetadataResult


METADATA_TIER1_PROMPT = """Extract structured metadata from this document chunk:

Content:
{content}

Provide metadata tags including:
- topic: Main topic or subject
- keywords: List of key terms
- density: technical|general|simple

Return JSON format."""

METADATA_TIER2_PROMPT = """Perform detailed technical analysis of this document chunk:

Content:
{content}

Provide comprehensive metadata including:
- topic: Specific technical topic
- keywords: Detailed technical terms
- density: Precise classification of technical depth
- concepts: Key concepts and relationships
- complexity_score: 1-10 rating
- domain: Technical domain

Return JSON format."""


class MetadataNode:
    """Metadata generation node with tiered approach (HLD section 2).

    Tier 1 (Small Local): Standard tagging for 90% of chunks
    Tier 2 (70B / Frontier): Used for technical density or borderline Judge scores (3-4)
    Cost Guardrail: Configurable hard cap of N Tier-2 chunks per document
    """

    def __init__(
        self,
        *,
        tier1_provider: ILlmProvider,
        tier1_model: str,
        tier2_provider: ILlmProvider | None = None,
        tier2_model: str | None = None,
        tier2_cap_per_doc: int = 25,
    ):
        self.tier1_provider = tier1_provider
        self.tier1_model = tier1_model
        self.tier2_provider = tier2_provider
        self.tier2_model = tier2_model
        self.tier2_cap = tier2_cap_per_doc
        self.diagnostics = get_diagnostics()

    async def generate_metadata(
        self,
        *,
        content: str,
        judge_score: float | None = None,
        tier2_count: int = 0,
        force_tier: int | None = None,
    ) -> MetadataResult:
        """Generate metadata for a chunk using appropriate tier.

        Args:
            content: The chunk content
            judge_score: Judge score (3-4 may trigger tier 2)
            tier2_count: Current tier 2 chunks used in document
            force_tier: Force specific tier (1 or 2)

        Returns:
            MetadataResult with generated tags and tier used
        """
        # Determine which tier to use
        tier = force_tier if force_tier else self._select_tier(judge_score, tier2_count)

        with self.diagnostics.trace_operation(
            "generate_metadata", {"tier": tier, "content_length": len(content)}
        ):
            if tier == 2 and self.tier2_provider and self.tier2_model:
                return await self._generate_tier2(content)
            return await self._generate_tier1(content)

    def _select_tier(self, judge_score: float | None, tier2_count: int) -> int:
        """Select metadata tier based on rules (HLD section 2).

        Tier 2 used for:
        - Technical density indicators
        - Borderline Judge scores (3-4)
        - Subject to cap limit
        """
        # Check if we've hit the cap
        if tier2_count >= self.tier2_cap:
            self.diagnostics.log_info(
                component="metadata",
                message=f"Tier 2 cap reached ({self.tier2_cap}), using tier 1",
            )
            return 1

        # Use tier 2 for borderline scores
        if judge_score is not None and 3 <= judge_score <= 4:
            self.diagnostics.log_info(
                component="metadata",
                message=f"Using tier 2 for borderline judge score: {judge_score}",
            )
            return 2

        # Default to tier 1
        return 1

    async def _generate_tier1(self, content: str) -> MetadataResult:
        """Generate Tier 1 metadata using small local model."""
        try:
            prompt = METADATA_TIER1_PROMPT.format(content=content[:1000])  # Limit length

            tags = await self._call_model(self.tier1_provider, self.tier1_model, prompt)
            tags = self._validate_metadata_schema(tags, tier=1)

            self.diagnostics.log_info(
                component="metadata",
                message="Generated tier 1 metadata",
                context={"tags": tags},
            )

            return MetadataResult(
                tags=tags,
                tier=1,
                model_used=self.tier1_model,
                timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            )

        except Exception as e:
            self.diagnostics.log_error(
                component="metadata",
                error_code=ErrorCode.METADATA_TIER1_FAILED,
                message="Tier 1 metadata generation failed",
                exception=e,
            )
            return MetadataResult(
                tags={"error": str(e)},
                tier=1,
                model_used=self.tier1_model,
                timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            )

    async def _generate_tier2(self, content: str) -> MetadataResult:
        """Generate Tier 2 metadata using frontier/70B model."""
        if not self.tier2_provider or not self.tier2_model:
            # Fallback to tier 1 when tier 2 is not configured.
            self.diagnostics.log_info(
                component="metadata",
                message="Tier 2 model not configured; falling back to tier 1",
            )
            return await self._generate_tier1(content)

        try:
            prompt = METADATA_TIER2_PROMPT.format(content=content[:2000])  # More context

            tags = await self._call_model(self.tier2_provider, self.tier2_model, prompt)
            tags = self._validate_metadata_schema(tags, tier=2)

            self.diagnostics.log_info(
                component="metadata",
                message="Generated tier 2 metadata",
                context={"tags": tags},
            )

            return MetadataResult(
                tags=tags,
                tier=2,
                model_used=self.tier2_model,
                timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            )

        except Exception as e:
            self.diagnostics.log_error(
                component="metadata",
                error_code=ErrorCode.METADATA_TIER2_FAILED,
                message="Tier 2 metadata generation failed, falling back to tier 1",
                exception=e,
            )
            # Fallback to tier 1 on error
            return await self._generate_tier1(content)

    async def _call_model(
        self, provider: ILlmProvider, model: str, prompt: str
    ) -> dict[str, Any]:
        """Call model to generate metadata, returning a validated JSON dict."""
        messages = [
            ChatMessage(role="system", content="You extract metadata and return JSON only."),
            ChatMessage(role="user", content=prompt),
        ]
        raw = await provider.chat(model=model, messages=messages, params={})
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except Exception as e:  # noqa: BLE001
            self.diagnostics.log_warning(
                component="metadata",
                message=f"Failed to parse metadata JSON, returning fallback tags: {e}",
            )
        return {
            "topic": "unparsed",
            "keywords": ["unparsed"],
            "density": "general",
            "raw": raw[:5000],
        }

    def _validate_metadata_schema(self, tags: dict[str, Any], *, tier: int) -> dict[str, Any]:
        """Ensure required metadata fields are present with sensible defaults.

        Required fields: topic (str), keywords (list[str]), density (str).
        Tier-2 additionally expects: concepts, complexity_score, domain.
        """
        _REQUIRED_TIER1 = {"topic", "keywords", "density"}
        _REQUIRED_TIER2 = {"concepts", "complexity_score", "domain"}

        defaults: dict[str, Any] = {
            "topic": "unknown",
            "keywords": [],
            "density": "general",
        }
        if tier >= 2:
            defaults.update({"concepts": [], "complexity_score": 5, "domain": "general"})

        required = _REQUIRED_TIER1 | (_REQUIRED_TIER2 if tier >= 2 else set())
        missing = required - tags.keys()
        if missing:
            self.diagnostics.log_warning(
                component="metadata",
                message=f"Metadata response missing required fields: {missing}; applying defaults",
                context={"tier": tier, "missing": sorted(missing)},
            )
            for k in missing:
                tags[k] = defaults[k]

        # Coerce keywords/concepts to lists if present but wrong type.
        for list_field in ("keywords", "concepts"):
            if list_field in tags:
                val = tags[list_field]
                if isinstance(val, list):
                    pass  # already correct
                elif isinstance(val, str):
                    # Split comma-separated strings into a list.
                    tags[list_field] = [v.strip() for v in val.split(",") if v.strip()]
                else:
                    # Non-string, non-list: convert to a single-element list.
                    tags[list_field] = [val]

        return tags
