"""Tests for PipelineOrchestrator — node routing and end-to-end flow."""

from __future__ import annotations

import pytest

from atlas.llm.deterministic import DeterministicProvider
from atlas.pipeline.cleanup import CleanupNode
from atlas.pipeline.judge import JudgeNode
from atlas.pipeline.ingest import IngestNode
from atlas.pipeline.metadata import MetadataNode
from atlas.pipeline.orchestrator import PipelineOrchestrator
from atlas.pipeline.refine import RefineNode
from atlas.pipeline.state import PipelineContext, PipelineNode, create_pipeline_context


def _build_orchestrator(*, judge_cutoff: int = 4) -> PipelineOrchestrator:
    provider = DeterministicProvider()
    return PipelineOrchestrator(
        ingest_node=IngestNode(),
        cleanup_node=CleanupNode(),
        judge_node=JudgeNode(provider=provider, model_name="det-judge", model_params={}),
        refine_node=RefineNode(provider=provider, model_name="det-refine", model_params={}),
        metadata_node=MetadataNode(tier1_provider=provider, tier1_model="det-meta"),
        config={"thresholds": {"judge_cutoff_refine": judge_cutoff}},
    )


def _make_context(markdown: str = "# Test\n\nSome content.") -> PipelineContext:
    ctx = create_pipeline_context(
        doc_id="test-doc",
        doc_version="v1",
        tenant_id="t1",
        project_id="p1",
        corpus_id="c1",
    )
    ctx.state.markdown_projection = markdown
    return ctx


class TestOrchestratorInit:
    def test_creates_with_all_nodes(self) -> None:
        orch = _build_orchestrator()
        assert orch.ingest_node is not None
        assert orch.judge_node is not None
        assert orch.refine_node is not None
        assert orch.metadata_node is not None

    def test_default_cleanup_node(self) -> None:
        provider = DeterministicProvider()
        orch = PipelineOrchestrator(
            ingest_node=IngestNode(),
            cleanup_node=None,
            judge_node=JudgeNode(provider=provider, model_name="m", model_params={}),
            refine_node=RefineNode(provider=provider, model_name="m", model_params={}),
            metadata_node=MetadataNode(tier1_provider=provider, tier1_model="m"),
            config={},
        )
        assert orch.cleanup_node is not None


class TestProcessIngest:
    @pytest.mark.asyncio
    async def test_empty_markdown_fails(self) -> None:
        orch = _build_orchestrator()
        ctx = _make_context(markdown="")
        await orch._process_ingest(ctx)
        assert ctx.state.error_code is not None


class TestProcessJudge:
    @pytest.mark.asyncio
    async def test_judge_stores_result(self) -> None:
        orch = _build_orchestrator()
        ctx = _make_context()
        await orch._process_judge(ctx)
        assert "judge" in ctx.results
        assert "score" in ctx.results["judge"]

    @pytest.mark.asyncio
    async def test_judge_sets_mean_score(self) -> None:
        orch = _build_orchestrator()
        ctx = _make_context()
        await orch._process_judge(ctx)
        assert ctx.state.mean_judge_score is not None


class TestProcessMetadata:
    @pytest.mark.asyncio
    async def test_metadata_stores_result(self) -> None:
        orch = _build_orchestrator()
        ctx = _make_context()
        await orch._process_metadata(ctx)
        assert "metadata" in ctx.results
