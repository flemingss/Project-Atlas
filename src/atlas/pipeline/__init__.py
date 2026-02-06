"""Pipeline module for Project Atlas agentic processing (HLD section 2).

Implements the document processing pipeline:
Ingest → Judge → Refine → Metadata → Embeddings → Chunking → Commit
"""

from atlas.pipeline.ingest import IngestNode, IngestResult
from atlas.pipeline.judge import JudgeNode
from atlas.pipeline.metadata import MetadataNode
from atlas.pipeline.orchestrator import PipelineOrchestrator
from atlas.pipeline.refine import RefineNode
from atlas.pipeline.state import (
    PipelineContext,
    PipelineNode,
    PipelineStateManager,
    create_pipeline_context,
)

__all__ = [
    "IngestNode",
    "IngestResult",
    "JudgeNode",
    "MetadataNode",
    "PipelineOrchestrator",
    "RefineNode",
    "PipelineContext",
    "PipelineNode",
    "PipelineStateManager",
    "create_pipeline_context",
]
