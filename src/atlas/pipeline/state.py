"""Pipeline state management for Project Atlas agentic loop (HLD section 2).

Manages document processing state through the pipeline nodes:
Ingest → Judge → Refine → Embeddings → Chunking → Commit
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from atlas.schemas import DocumentIngestState, JudgeResult, MetadataResult, RefineResult


class PipelineNode(str, Enum):
    """Pipeline node states (HLD section 2: Node Flow & Logic)."""

    INGEST = "ingest"
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
        """Store judge result in context."""
        self.results["judge"] = asdict(result)
        self.state.mean_judge_score = result.score

    def set_refine_result(self, result: RefineResult) -> None:
        """Store refine result in context."""
        self.results["refine"] = asdict(result)
        self.state.refine_retries += 1
        if result.success:
            self.state.markdown_projection = result.refined_markdown

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
            PipelineNode.INGEST: [PipelineNode.JUDGE, PipelineNode.FAILED],
            PipelineNode.JUDGE: [
                PipelineNode.REFINE,
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
            context.state.completed_at = datetime.utcnow().isoformat() + "Z"

        return True

    def get_next_node(
        self, context: PipelineContext, config: dict[str, Any]
    ) -> PipelineNode | None:
        """Determine the next pipeline node based on current state and config."""
        current_node = PipelineNode(context.state.current_node)

        if current_node == PipelineNode.INGEST:
            return PipelineNode.JUDGE

        if current_node == PipelineNode.JUDGE:
            judge_cutoff = config.get("thresholds", {}).get("judge_cutoff_refine", 4)
            if context.should_refine(judge_cutoff) and context.can_retry_refine():
                return PipelineNode.REFINE
            if context.needs_hitl_review():
                return PipelineNode.HITL
            return PipelineNode.METADATA

        if current_node == PipelineNode.REFINE:
            # After refine, go back to judge for re-evaluation
            return PipelineNode.JUDGE

        if current_node == PipelineNode.METADATA:
            return PipelineNode.EMBEDDINGS

        if current_node == PipelineNode.EMBEDDINGS:
            return PipelineNode.CHUNKING

        if current_node == PipelineNode.CHUNKING:
            return PipelineNode.COMMIT

        if current_node == PipelineNode.COMMIT:
            return PipelineNode.COMPLETED

        if current_node == PipelineNode.HITL:
            # After HITL, could go back to judge or complete
            return PipelineNode.COMPLETED

        return None


def create_pipeline_context(
    *,
    doc_id: str,
    doc_version: str,
    tenant_id: str,
    project_id: str,
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
        source_uri=source_uri,
        source_mime_type=source_mime_type,
        max_refine_retries=max_refine_retries,
        started_at=datetime.utcnow().isoformat() + "Z",
    )
    return PipelineContext(state=state)
