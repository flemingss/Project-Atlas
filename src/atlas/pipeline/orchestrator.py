"""Pipeline orchestration for Project Atlas (HLD section 2).

Coordinates the agentic loop: Ingest → Judge → Refine → Metadata → Embeddings → Chunking → Commit
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from atlas.diagnostics import ErrorCode, get_diagnostics
from atlas.pipeline.cleanup import CleanupNode
from atlas.pipeline.ingest import IngestNode
from atlas.pipeline.judge import JudgeNode
from atlas.pipeline.metadata import MetadataNode
from atlas.pipeline.refine import RefineNode
from atlas.pipeline.state import PipelineContext, PipelineNode, PipelineStateManager
from atlas.pipeline.tokens import estimate_tokens, fits_in_context
from atlas.schemas import JudgeResult


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
        cleanup_node: CleanupNode | None = None,
        judge_node: JudgeNode,
        refine_node: RefineNode,
        metadata_node: MetadataNode,
        config: dict[str, Any],
    ):
        self.ingest_node = ingest_node
        self.cleanup_node = cleanup_node or CleanupNode()
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
            elif current_node == PipelineNode.CLEANUP:
                await self._process_cleanup(context)
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
                    error_code=ErrorCode.PIPELINE_FAILED,
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
                    error_code=ErrorCode.PIPELINE_INVALID_TRANSITION,
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
            context.state.error_code = ErrorCode.INGEST_FAILED.value
            self.state_manager.transition(context, PipelineNode.FAILED)

    async def _process_cleanup(self, context: PipelineContext) -> None:
        """Process cleanup node — deterministic markdown transforms."""
        self.diagnostics.log_info(component="pipeline", message="Processing cleanup node")

        # Build doc context for config-driven rule matching
        doc_ctx = {
            "tenant_id": context.state.tenant_id,
            "project_id": context.state.project_id,
            "corpus_id": getattr(context.state, "corpus_id", ""),
            "mime_type": context.state.source_mime_type,
            "filename": getattr(context.state, "source_uri", "") or "",
            "parse_profile": str(context.state.parse_profile or ""),
        }

        result = await self.cleanup_node.clean(
            markdown=context.state.markdown_projection,
            doc_context=doc_ctx,
            config=self.config,
        )
        context.set_cleanup_result(result)

    async def _process_judge(self, context: PipelineContext) -> None:
        """Process judge node."""
        self.diagnostics.log_info(component="pipeline", message="Processing judge node")

        judge_cutoff = self.config.get("thresholds", {}).get("judge_cutoff_refine", 4)
        markdown = context.state.markdown_projection

        # Oversize guard. The judge prompt embeds the whole document, so a very
        # large manual would produce an over-length request — a non-retryable
        # 4xx that fails the entire ingest. Losing the document over a quality
        # check it is too big to receive is the wrong trade: skip the check,
        # say so loudly, and let it continue to metadata so it still ends up
        # chunked, embedded and searchable.
        judge_ctx = getattr(self.judge_node, "max_context_tokens", None)
        # output_ratio=0: the judge emits a handful of scores, not a rewrite.
        # The overhead covers the system rubric and few-shot examples.
        if judge_ctx and not fits_in_context(
            markdown, judge_ctx, prompt_overhead_tokens=2000, output_ratio=0.0
        ):
            self.diagnostics.log_info(
                component="pipeline",
                message=(
                    f"Document exceeds judge context budget "
                    f"(~{estimate_tokens(markdown)} tokens > {judge_ctx}); "
                    "skipping quality grading. The document is NOT quality-gated "
                    "and will not be refined; it proceeds to metadata and indexing."
                ),
            )
            context.set_judge_result(
                JudgeResult(
                    score=judge_cutoff,
                    confidence_rationale=(
                        "SKIPPED: document too large for the judge model's context "
                        f"(~{estimate_tokens(markdown)} tokens, budget {judge_ctx}). "
                        "Not graded — score reported at cutoff so ingest continues."
                    ),
                    judge_version=f"skipped-oversize:{getattr(self.judge_node, 'model_name', '?')}",
                    needs_refinement=False,
                    timestamp=_dt.datetime.now(_dt.UTC)
                    .isoformat()
                    .replace("+00:00", "Z"),
                )
            )
            return

        result = await self.judge_node.grade_document(
            markdown=markdown, judge_cutoff=judge_cutoff
        )

        context.set_judge_result(result)

    async def _process_refine(self, context: PipelineContext) -> None:
        """Process refine node.

        Chooses between full-document and sectional refinement based on
        whether the document fits in the refine model's context window.
        """
        self.diagnostics.log_info(component="pipeline", message="Processing refine node")

        judge_result = context.results.get("judge", {})
        judge_score = judge_result.get("score", 3)
        judge_sub_scores = judge_result.get("sub_scores")
        judge_rationale = judge_result.get("confidence_rationale")
        max_retries = self.config.get("limits", {}).get(
            "refine_max_retries", context.state.max_refine_retries
        )

        markdown = context.state.markdown_projection
        max_ctx = int(self.config.get("limits", {}).get("max_context_tokens", 16384))
        # The refine model's own response ceiling. A 1M-context model that caps
        # responses at 48k cannot rewrite a 100k-token document in one pass, so
        # context budget alone is not enough to make this decision.
        max_out = getattr(self.refine_node, "max_output_tokens", None)

        if fits_in_context(markdown, max_ctx, max_output_tokens=max_out):
            # Full-document refinement — document fits in context
            result = await self.refine_node.refine_document(
                markdown=markdown,
                judge_score=judge_score,
                retry_count=context.state.refine_retries,
                max_retries=max_retries,
                judge_sub_scores=judge_sub_scores,
                judge_rationale=judge_rationale,
            )
        else:
            # Sectional refinement — document too long for one pass
            self.diagnostics.log_info(
                component="pipeline",
                message=(
                    f"Document exceeds budget for full refinement "
                    f"(~{len(markdown)} chars, max_ctx={max_ctx}, "
                    f"max_output={max_out}); using sectional refinement"
                ),
            )
            result = await self.refine_node.refine_document_sectional(
                markdown=markdown,
                judge_score=judge_score,
                retry_count=context.state.refine_retries,
                max_retries=max_retries,
                judge_sub_scores=judge_sub_scores,
                judge_rationale=judge_rationale,
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
