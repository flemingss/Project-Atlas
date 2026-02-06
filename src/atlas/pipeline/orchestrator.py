"""Pipeline orchestration for Project Atlas (HLD section 2).

Coordinates the agentic loop: Ingest → Judge → Refine → Metadata → Embeddings → Chunking → Commit
"""

from __future__ import annotations

from typing import Any

from atlas.diagnostics import get_diagnostics
from atlas.pipeline.ingest import IngestNode
from atlas.pipeline.judge import JudgeNode
from atlas.pipeline.metadata import MetadataNode
from atlas.pipeline.refine import RefineNode
from atlas.pipeline.state import PipelineContext, PipelineNode, PipelineStateManager


class PipelineOrchestrator:
    """Orchestrates the document processing pipeline (HLD section 2: Agentic Loop).

    Flow:
    1. Ingest: Convert documents to structured format
    2. Judge: Grade quality (1-5 scale)
    3. Refine: Improve low-quality docs (if score < threshold)
    4. Metadata: Generate tiered metadata tags
    5. Embeddings: Generate vectors
    6. Chunking: Split with hierarchical awareness
    7. Commit: Store in vector database

    HITL checkpoint triggered when:
    - Refine max retries exceeded
    - Low judge scores persist
    """

    def __init__(
        self,
        *,
        ingest_node: IngestNode,
        judge_node: JudgeNode,
        refine_node: RefineNode,
        metadata_node: MetadataNode,
        config: dict[str, Any],
    ):
        self.ingest_node = ingest_node
        self.judge_node = judge_node
        self.refine_node = refine_node
        self.metadata_node = metadata_node
        self.config = config
        self.state_manager = PipelineStateManager()
        self.diagnostics = get_diagnostics()

    async def process_document(self, context: PipelineContext) -> PipelineContext:
        """Process a document through the pipeline.

        Args:
            context: Pipeline context with document state

        Returns:
            Updated context after processing
        """
        self.diagnostics.log_info(
            component="pipeline",
            message=f"Starting pipeline for doc {context.state.doc_id}",
            context={"doc_id": context.state.doc_id},
        )

        while not context.state.is_completed:
            current_node = PipelineNode(context.state.current_node)

            # Process current node
            if current_node == PipelineNode.INGEST:
                await self._process_ingest(context)
            elif current_node == PipelineNode.JUDGE:
                await self._process_judge(context)
            elif current_node == PipelineNode.REFINE:
                await self._process_refine(context)
            elif current_node == PipelineNode.METADATA:
                await self._process_metadata(context)
            elif current_node == PipelineNode.HITL:
                # HITL requires manual intervention - stop pipeline
                self.diagnostics.log_info(
                    component="pipeline",
                    message="Pipeline paused for HITL review",
                )
                break
            elif current_node == PipelineNode.FAILED:
                self.diagnostics.log_error(
                    component="pipeline",
                    error_code=None,
                    message="Pipeline failed",
                    context={"doc_id": context.state.doc_id},
                )
                break
            else:
                # Embeddings, Chunking, Commit handled separately
                self.diagnostics.log_info(
                    component="pipeline",
                    message=f"Node {current_node} requires external processing",
                )
                break

            # Get next node
            next_node = self.state_manager.get_next_node(context, self.config)
            if not next_node:
                break

            # Transition
            if not self.state_manager.transition(context, next_node):
                self.diagnostics.log_error(
                    component="pipeline",
                    error_code=None,
                    message=f"Invalid transition from {current_node} to {next_node}",
                )
                break

        return context

    async def _process_ingest(self, context: PipelineContext) -> None:
        """Process ingest node."""
        self.diagnostics.log_info(component="pipeline", message="Processing ingest node")

        # For now, assume markdown is already in state
        # Full implementation would call ingest_node.process_document()
        if not context.state.markdown_projection:
            context.state.error_code = "INGEST_FAILED"
            context.state.current_node = PipelineNode.FAILED.value

    async def _process_judge(self, context: PipelineContext) -> None:
        """Process judge node."""
        self.diagnostics.log_info(component="pipeline", message="Processing judge node")

        judge_cutoff = self.config.get("thresholds", {}).get("judge_cutoff_refine", 4)

        result = await self.judge_node.grade_document(
            markdown=context.state.markdown_projection, judge_cutoff=judge_cutoff
        )

        context.set_judge_result(result)

    async def _process_refine(self, context: PipelineContext) -> None:
        """Process refine node."""
        self.diagnostics.log_info(component="pipeline", message="Processing refine node")

        judge_result = context.results.get("judge", {})
        judge_score = judge_result.get("score", 3)

        result = await self.refine_node.refine_document(
            markdown=context.state.markdown_projection,
            judge_score=judge_score,
            retry_count=context.state.refine_retries,
        )

        context.set_refine_result(result)

    async def _process_metadata(self, context: PipelineContext) -> None:
        """Process metadata node."""
        self.diagnostics.log_info(component="pipeline", message="Processing metadata node")

        result = await self.metadata_node.generate_metadata(
            content=context.state.markdown_projection,
            judge_score=context.state.mean_judge_score,
            tier2_count=context.state.tier2_chunks_used,
        )

        context.set_metadata_result(result)
