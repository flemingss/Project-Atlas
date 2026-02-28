"""Tests for enhanced data schemas."""


from atlas.schemas import ChunkMetadata, FidelityFlag, JudgeResult, ParseProfile


def test_chunk_metadata_creation():
    """Test creating chunk metadata with required fields."""
    metadata = ChunkMetadata(
        tenant_id="test-tenant",
        project_id="test-project",
        doc_id="doc-123",
        doc_version="1",
        chunk_index=0,
        text="Sample text",
        content_hash="abc123",
    )

    assert metadata.tenant_id == "test-tenant"
    assert metadata.chunk_index == 0
    assert metadata.is_finalized is False
    assert metadata.is_sensitive is True  # Default
    assert metadata.metadata_tier == 1  # Default


def test_chunk_metadata_with_hierarchical_info():
    """Test chunk metadata with section path and parent info."""
    metadata = ChunkMetadata(
        tenant_id="test-tenant",
        project_id="test-project",
        doc_id="doc-123",
        doc_version="1",
        chunk_index=0,
        text="Sample text",
        content_hash="abc123",
        parent_header_id="header-1",
        sibling_ids=["chunk-1", "chunk-2"],
        section_path=["Chapter 1", "Introduction"],
    )

    assert metadata.parent_header_id == "header-1"
    assert len(metadata.sibling_ids) == 2
    assert metadata.section_path == ["Chapter 1", "Introduction"]


def test_judge_result_creation():
    """Test creating judge result."""
    result = JudgeResult(
        score=4,
        confidence_rationale="Good structure and clarity",
        judge_version="llama-3.2-3b:v1",
        needs_refinement=False,
        timestamp="2024-01-01T00:00:00Z",
        sub_scores={"faithfulness": 5, "formatting": 4, "cohesion": 4, "hallucination_risk": 4},
    )

    assert result.score == 4
    assert result.needs_refinement is False
    assert "Good structure" in result.confidence_rationale
    assert result.sub_scores["faithfulness"] == 5
    assert len(result.sub_scores) == 4


def test_judge_result_default_sub_scores():
    """Test JudgeResult defaults to empty sub_scores for backwards compat."""
    result = JudgeResult(
        score=3,
        confidence_rationale="test",
        judge_version="v1",
        needs_refinement=True,
        timestamp="2024-01-01T00:00:00Z",
    )
    assert result.sub_scores == {}


def test_fidelity_flags():
    """Test fidelity flag enum values."""
    assert FidelityFlag.VERIFIED == "verified"
    assert FidelityFlag.LOW_CONFIDENCE == "low_confidence"
    assert FidelityFlag.NEEDS_REVIEW == "needs_review"


def test_parse_profile_values():
    """Test parse profile enum values."""
    assert ParseProfile.PDF_TEXT == "pdf_text"
    assert ParseProfile.PDF_LAYOUT == "pdf_layout"
    assert ParseProfile.MARKDOWN == "markdown"
    assert ParseProfile.TEXT == "text"
