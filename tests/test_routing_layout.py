"""Tests for routing with layout parser extraction_meta.

Verifies that ``decide_next_step`` considers OCR confidence from the
layout parser when making ingest→cleanup routing decisions.
"""

from __future__ import annotations

from atlas.pipeline.routing import decide_next_step


def _cfg(**overrides) -> dict:
    """Minimal pipeline config with overridable thresholds."""
    thresholds = {"judge_cutoff_refine": 4, "fail_fast_score": 0}
    thresholds.update(overrides)
    return {"thresholds": thresholds}


def _state(**overrides) -> dict:
    base = {"refine_retries": 0, "max_refine_retries": 2, "needs_hitl": False}
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# OCR confidence routing
# ---------------------------------------------------------------------------

def test_routing_ingest_with_low_ocr_confidence_fails():
    """Critically low OCR confidence + fail_fast_score enabled → failed."""
    decision = decide_next_step(
        current_node="ingest",
        results={
            "extraction_meta": {"mean_ocr_confidence": 0.1},
        },
        state_snapshot=_state(),
        config=_cfg(fail_fast_score=1),
    )
    assert decision.target == "failed"
    assert "ocr" in decision.reason.lower() or "confidence" in decision.reason.lower()


def test_routing_ingest_with_good_ocr_confidence_proceeds():
    """High OCR confidence should proceed normally to cleanup."""
    decision = decide_next_step(
        current_node="ingest",
        results={
            "extraction_meta": {"mean_ocr_confidence": 0.9},
        },
        state_snapshot=_state(),
        config=_cfg(),
    )
    assert decision.target == "cleanup"


def test_routing_ingest_without_extraction_meta_proceeds():
    """No extraction_meta at all (e.g. Docling backend) → standard cleanup."""
    decision = decide_next_step(
        current_node="ingest",
        results={},
        state_snapshot=_state(),
        config=_cfg(),
    )
    assert decision.target == "cleanup"


def test_routing_ingest_borderline_ocr_confidence():
    """OCR confidence exactly at 0.3 (boundary) should NOT fail if >= threshold."""
    decision = decide_next_step(
        current_node="ingest",
        results={
            "extraction_meta": {"mean_ocr_confidence": 0.3},
        },
        state_snapshot=_state(),
        config=_cfg(fail_fast_score=1),
    )
    # 0.3 is NOT < 0.3, so it should proceed
    assert decision.target == "cleanup"


def test_routing_ingest_low_ocr_without_fail_fast():
    """Low OCR confidence but fail_fast_score=0 (disabled) → still proceeds."""
    decision = decide_next_step(
        current_node="ingest",
        results={
            "extraction_meta": {"mean_ocr_confidence": 0.05},
        },
        state_snapshot=_state(),
        config=_cfg(fail_fast_score=0),
    )
    assert decision.target == "cleanup"


def test_routing_ingest_with_extraction_meta_no_ocr_key():
    """extraction_meta present but missing mean_ocr_confidence key → proceeds."""
    decision = decide_next_step(
        current_node="ingest",
        results={
            "extraction_meta": {"layout_confidence": 0.9},
        },
        state_snapshot=_state(),
        config=_cfg(fail_fast_score=1),
    )
    assert decision.target == "cleanup"
