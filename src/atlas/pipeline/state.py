"""Pipeline state management for Project Atlas agentic loop (HLD section 2).

Manages document processing state through the pipeline nodes:
Ingest → Judge → Refine → Embeddings → Chunking → Commit
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from atlas.pipeline.routing import decide_next_step
from atlas.schemas import (
    CleanupResult,
    DocumentIngestState,
    JudgeResult,
    MetadataResult,
    RefineResult,
)


class PipelineNode(str, Enum):
    """Pipeline node states (HLD section 2: Node Flow & Logic)."""

    INGEST = "ingest"
    CLEANUP = "cleanup"
    JUDGE = "judge"
    REFINE = "refine"
    METADATA = "metadata"
    EMBEDDINGS = "embeddings"
    CHUNKING = "chunking"
    COMMIT = "commit"
    HITL = "hitl"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class PipelineContext:
    """Context carried through the pipeline for a document."""

    state: DocumentIngestState
    results: dict[str, Any] = field(default_factory=dict)

    def set_judge_result(self, result: JudgeResult) -> None:
        """Store judge result in context and record score history."""
        self.results["judge"] = asdict(result)
        self.state.mean_judge_score = result.score
        # Maintain judge score history for diminishing-returns / regression detection.
        history: list[int] = self.results.setdefault("judge_score_history", [])
        history.append(result.score)

    def set_cleanup_result(self, result: CleanupResult) -> None:
        """Store cleanup result and update markdown projection in state."""
        self.results["cleanup"] = asdict(result)
        self.state.markdown_projection = result.cleaned_markdown
        # Track cleanup_rejudge cycles (judge→cleanup→judge).
        # Incremented each time cleanup runs after the initial pass.
        cleanup_count = self.results.get("cleanup_rejudge_count", 0)
        if self.state.current_node == "cleanup" and cleanup_count >= 0:
            # Only count re-cleanup cycles (not the initial cleanup).
            judge_history = self.results.get("judge_score_history", [])
            if judge_history:
                # A judge has already run → this is a re-cleanup.
                self.results["cleanup_rejudge_count"] = cleanup_count + 1

    def set_docling_health(self, health: dict[str, Any]) -> None:
        """Store Docling health assessment in context for downstream routing."""
        self.results["docling_health"] = health

    def set_refine_result(self, result: RefineResult) -> None:
        """Store refine result in context.

        Only counts the attempt as a burned retry if the refinement
        actually succeeded (produced new text).  Failed refinements
        (model error, preservation guardrail) do NOT consume a retry
        slot so the document gets another chance — but we track total
        attempts to prevent infinite failure loops.
        """
        self.results["refine"] = asdict(result)
        # Save pre-refine markdown for potential regression rollback.
        self.results["pre_refine_markdown"] = self.state.markdown_projection
        # Track total attempts (successes + failures) as a circuit-breaker.
        total_attempts = self.results.get("refine_total_attempts", 0)
        self.results["refine_total_attempts"] = total_attempts + 1
        if result.success:
            self.state.refine_retries += 1
            self.state.markdown_projection = result.refined_markdown
        elif total_attempts + 1 >= self.state.max_refine_retries * 2:
            # Hard cap: if total attempts (including failures) reaches 2× max,
            # count it to force exit from the refine loop.
            self.state.refine_retries += 1

    def set_metadata_result(self, result: MetadataResult) -> None:
        """Store metadata result in context."""
        self.results["metadata"] = asdict(result)
        if result.tier == 2:
            self.state.tier2_chunks_used += 1

    def should_refine(self, judge_cutoff: int) -> bool:
        """Determine if document needs refinement based on judge score."""
        judge_result = self.results.get("judge")
        if not judge_result:
            return False
        return judge_result["score"] < judge_cutoff

    def can_retry_refine(self) -> bool:
        """Check if refinement can be retried."""
        return self.state.refine_retries < self.state.max_refine_retries

    def needs_hitl_review(self) -> bool:
        """Determine if document needs HITL review."""
        return self.state.needs_hitl or self.state.refine_retries >= self.state.max_refine_retries


class PipelineStateManager:
    """Manages pipeline state transitions and validation."""

    def __init__(self):
        self.valid_transitions: dict[PipelineNode, list[PipelineNode]] = {
            PipelineNode.INGEST: [PipelineNode.CLEANUP, PipelineNode.FAILED],
            PipelineNode.CLEANUP: [PipelineNode.JUDGE, PipelineNode.HITL, PipelineNode.FAILED],
            PipelineNode.JUDGE: [
                PipelineNode.REFINE,
                PipelineNode.CLEANUP,
                PipelineNode.METADATA,
                PipelineNode.HITL,
                PipelineNode.FAILED,
            ],
            PipelineNode.REFINE: [PipelineNode.JUDGE, PipelineNode.HITL, PipelineNode.FAILED],
            PipelineNode.METADATA: [PipelineNode.EMBEDDINGS, PipelineNode.FAILED],
            PipelineNode.EMBEDDINGS: [PipelineNode.CHUNKING, PipelineNode.FAILED],
            PipelineNode.CHUNKING: [PipelineNode.COMMIT, PipelineNode.FAILED],
            PipelineNode.COMMIT: [PipelineNode.COMPLETED, PipelineNode.FAILED],
            PipelineNode.HITL: [PipelineNode.JUDGE, PipelineNode.COMPLETED],
            PipelineNode.COMPLETED: [],
            PipelineNode.FAILED: [],
        }

    def can_transition(self, from_node: PipelineNode, to_node: PipelineNode) -> bool:
        """Check if state transition is valid."""
        return to_node in self.valid_transitions.get(from_node, [])

    def transition(self, context: PipelineContext, to_node: PipelineNode) -> bool:
        """Transition to a new pipeline node if valid."""
        current_node = PipelineNode(context.state.current_node)

        if not self.can_transition(current_node, to_node):
            return False

        context.state.current_node = to_node.value

        if to_node == PipelineNode.COMPLETED:
            context.state.is_completed = True
            context.state.completed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        return True

    def get_next_node(
        self, context: PipelineContext, config: dict[str, Any]
    ) -> PipelineNode | None:
        """Determine the next pipeline node based on current state and config.

        Delegates to :func:`atlas.pipeline.routing.decide_next_step` for all
        routing logic; this method translates the result back into a
        ``PipelineNode``.

        Side-effect: if routing detects a score regression after refine,
        it routes to ``metadata`` and this method rolls back the markdown
        to the pre-refine version.
        """
        decision = decide_next_step(
            current_node=context.state.current_node,
            results=context.results,
            state_snapshot={
                "refine_retries": context.state.refine_retries,
                "max_refine_retries": context.state.max_refine_retries,
                "needs_hitl": context.state.needs_hitl,
                "mean_judge_score": context.state.mean_judge_score,
                "judge_score_history": context.results.get("judge_score_history", []),
                "cleanup_rejudge_count": context.results.get("cleanup_rejudge_count", 0),
                "markdown_len": len(context.state.markdown_projection or ""),
                "is_hitl_resume": context.results.get("is_hitl_resume", False),
            },
            config=config,
        )

        # M7: Regression rollback — revert markdown to pre-refine state
        # when routing detects that the refine attempt made things worse.
        if decision.rollback:
            pre_refine_md = context.results.get("pre_refine_markdown")
            if pre_refine_md:
                context.state.markdown_projection = pre_refine_md

        try:
            return PipelineNode(decision.target)
        except ValueError:
            return None


def create_pipeline_context(
    *,
    doc_id: str,
    doc_version: str,
    tenant_id: str,
    project_id: str,
    corpus_id: str = "",
    source_uri: str | None = None,
    source_mime_type: str = "text/plain",
    max_refine_retries: int = 2,
) -> PipelineContext:
    """Create a new pipeline context for document processing."""
    state = DocumentIngestState(
        doc_id=doc_id,
        doc_version=doc_version,
        tenant_id=tenant_id,
        project_id=project_id,
        corpus_id=corpus_id,
        source_uri=source_uri,
        source_mime_type=source_mime_type,
        max_refine_retries=max_refine_retries,
        started_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )
    return PipelineContext(state=state)
