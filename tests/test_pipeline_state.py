"""Tests for pipeline state management."""


from atlas.pipeline.state import (
    PipelineNode,
    PipelineStateManager,
    create_pipeline_context,
)
from atlas.schemas import JudgeResult


def test_create_pipeline_context():
    """Test creating pipeline context."""
    context = create_pipeline_context(
        doc_id="doc-123",
        doc_version="1",
        tenant_id="test-tenant",
        project_id="test-project",
    )

    assert context.state.doc_id == "doc-123"
    assert context.state.current_node == "ingest"
    assert context.state.is_completed is False
    assert len(context.results) == 0


def test_set_judge_result():
    """Test setting judge result in context."""
    context = create_pipeline_context(
        doc_id="doc-123",
        doc_version="1",
        tenant_id="test-tenant",
        project_id="test-project",
    )

    result = JudgeResult(
        score=4,
        confidence_rationale="Good quality",
        judge_version="v1",
        needs_refinement=False,
        timestamp="2024-01-01T00:00:00Z",
    )

    context.set_judge_result(result)

    assert "judge" in context.results
    assert context.state.mean_judge_score == 4


def test_should_refine():
    """Test refinement decision logic."""
    context = create_pipeline_context(
        doc_id="doc-123",
        doc_version="1",
        tenant_id="test-tenant",
        project_id="test-project",
    )

    # Set a low judge score
    result = JudgeResult(
        score=3,
        confidence_rationale="Needs work",
        judge_version="v1",
        needs_refinement=True,
        timestamp="2024-01-01T00:00:00Z",
    )
    context.set_judge_result(result)

    # Should refine with cutoff of 4
    assert context.should_refine(judge_cutoff=4) is True

    # Should not refine with cutoff of 3
    assert context.should_refine(judge_cutoff=3) is False


def test_can_retry_refine():
    """Test refine retry logic."""
    context = create_pipeline_context(
        doc_id="doc-123",
        doc_version="1",
        tenant_id="test-tenant",
        project_id="test-project",
        max_refine_retries=2,
    )

    assert context.can_retry_refine() is True

    # Simulate two refine attempts
    context.state.refine_retries = 2
    assert context.can_retry_refine() is False


def test_pipeline_state_manager_valid_transitions():
    """Test valid state transitions."""
    manager = PipelineStateManager()

    assert manager.can_transition(PipelineNode.INGEST, PipelineNode.JUDGE) is True
    assert manager.can_transition(PipelineNode.JUDGE, PipelineNode.REFINE) is True
    assert manager.can_transition(PipelineNode.JUDGE, PipelineNode.METADATA) is True
    assert manager.can_transition(PipelineNode.REFINE, PipelineNode.JUDGE) is True


def test_pipeline_state_manager_invalid_transitions():
    """Test invalid state transitions."""
    manager = PipelineStateManager()

    assert manager.can_transition(PipelineNode.INGEST, PipelineNode.METADATA) is False
    assert manager.can_transition(PipelineNode.COMPLETED, PipelineNode.INGEST) is False
    assert manager.can_transition(PipelineNode.EMBEDDINGS, PipelineNode.JUDGE) is False


def test_pipeline_transition():
    """Test executing state transition."""
    manager = PipelineStateManager()
    context = create_pipeline_context(
        doc_id="doc-123",
        doc_version="1",
        tenant_id="test-tenant",
        project_id="test-project",
    )

    # Valid transition
    success = manager.transition(context, PipelineNode.JUDGE)
    assert success is True
    assert context.state.current_node == "judge"

    # Invalid transition
    success = manager.transition(context, PipelineNode.EMBEDDINGS)
    assert success is False


def test_get_next_node():
    """Test determining next pipeline node."""
    manager = PipelineStateManager()
    config = {"thresholds": {"judge_cutoff_refine": 4}}

    # Test ingest -> judge
    context = create_pipeline_context(
        doc_id="doc-123",
        doc_version="1",
        tenant_id="test-tenant",
        project_id="test-project",
    )
    next_node = manager.get_next_node(context, config)
    assert next_node == PipelineNode.JUDGE

    # Test judge -> metadata (good score)
    context.state.current_node = "judge"
    result = JudgeResult(
        score=5,
        confidence_rationale="Excellent",
        judge_version="v1",
        needs_refinement=False,
        timestamp="2024-01-01T00:00:00Z",
    )
    context.set_judge_result(result)
    next_node = manager.get_next_node(context, config)
    assert next_node == PipelineNode.METADATA

    # Test judge -> refine (low score)
    context2 = create_pipeline_context(
        doc_id="doc-124",
        doc_version="1",
        tenant_id="test-tenant",
        project_id="test-project",
    )
    context2.state.current_node = "judge"
    result2 = JudgeResult(
        score=2,
        confidence_rationale="Needs work",
        judge_version="v1",
        needs_refinement=True,
        timestamp="2024-01-01T00:00:00Z",
    )
    context2.set_judge_result(result2)
    next_node = manager.get_next_node(context2, config)
    assert next_node == PipelineNode.REFINE
