"""Tests for enhanced data schemas."""


from atlas.schemas import FidelityFlag, JudgeResult, ParseProfile


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
