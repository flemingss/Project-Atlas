"""Tests for atlas.pipeline.routing.decide_next_step."""

from __future__ import annotations

from atlas.pipeline.routing import RoutingDecision, decide_next_step


def _cfg(**overrides: object) -> dict:
    """Minimal pipeline config with overridable thresholds."""
    thresholds = {"judge_cutoff_refine": 4, "fail_fast_score": 0}
    thresholds.update(overrides)
    return {"thresholds": thresholds}


def _state(**overrides: object) -> dict:
    """Minimal state snapshot."""
    base = {"refine_retries": 0, "max_refine_retries": 2, "needs_hitl": False, "mean_judge_score": 0}
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# INGEST routing
# ---------------------------------------------------------------------------

def test_ingest_routes_to_cleanup():
    d = decide_next_step(current_node="ingest", results={}, state_snapshot=_state(), config=_cfg())
    assert d.target == "cleanup"


def test_ingest_fail_fast_on_bad_health():
    d = decide_next_step(
        current_node="ingest",
        results={"docling_health": {"health_score": 1}},
        state_snapshot=_state(),
        config=_cfg(fail_fast_score=2),
    )
    assert d.target == "failed"
    assert "health" in d.reason.lower()


def test_ingest_passes_good_health():
    d = decide_next_step(
        current_node="ingest",
        results={"docling_health": {"health_score": 4}},
        state_snapshot=_state(),
        config=_cfg(fail_fast_score=2),
    )
    assert d.target == "cleanup"


# ---------------------------------------------------------------------------
# CLEANUP routing
# ---------------------------------------------------------------------------

def test_cleanup_routes_to_judge():
    d = decide_next_step(current_node="cleanup", results={}, state_snapshot=_state(), config=_cfg())
    assert d.target == "judge"


# ---------------------------------------------------------------------------
# JUDGE routing — standard paths
# ---------------------------------------------------------------------------

def test_judge_good_score_to_metadata():
    d = decide_next_step(
        current_node="judge",
        results={"judge": {"score": 5, "sub_scores": {"faithfulness": 5, "formatting": 5, "cohesion": 5, "hallucination_risk": 5}}},
        state_snapshot=_state(),
        config=_cfg(),
    )
    assert d.target == "metadata"


def test_judge_low_score_to_refine():
    d = decide_next_step(
        current_node="judge",
        results={"judge": {"score": 2, "sub_scores": {}}},
        state_snapshot=_state(),
        config=_cfg(),
    )
    assert d.target == "refine"


def test_judge_low_score_retries_exhausted_to_hitl():
    d = decide_next_step(
        current_node="judge",
        results={"judge": {"score": 2, "sub_scores": {}}},
        state_snapshot=_state(refine_retries=2, max_refine_retries=2),
        config=_cfg(),
    )
    assert d.target == "hitl"


def test_judge_fail_fast():
    d = decide_next_step(
        current_node="judge",
        results={"judge": {"score": 1, "sub_scores": {}}},
        state_snapshot=_state(),
        config=_cfg(fail_fast_score=1),
    )
    assert d.target == "failed"


# ---------------------------------------------------------------------------
# JUDGE routing — cleanup_rejudge
# ---------------------------------------------------------------------------

def test_judge_cleanup_rejudge_when_formatting_bad():
    """Formatting low, but content (faithfulness + cohesion) OK → re-clean."""
    d = decide_next_step(
        current_node="judge",
        results={"judge": {
            "score": 3,
            "sub_scores": {"faithfulness": 5, "formatting": 2, "cohesion": 4, "hallucination_risk": 5},
        }},
        state_snapshot=_state(),
        config=_cfg(cleanup_rejudge=True),
    )
    assert d.target == "cleanup"
    assert "re-clean" in d.reason.lower()


def test_judge_no_cleanup_rejudge_when_disabled():
    """Same sub_scores but cleanup_rejudge disabled → standard refine."""
    d = decide_next_step(
        current_node="judge",
        results={"judge": {
            "score": 3,
            "sub_scores": {"faithfulness": 5, "formatting": 2, "cohesion": 4, "hallucination_risk": 5},
        }},
        state_snapshot=_state(),
        config=_cfg(cleanup_rejudge=False),
    )
    assert d.target == "refine"


# ---------------------------------------------------------------------------
# JUDGE routing — per-dimension floors
# ---------------------------------------------------------------------------

def test_judge_dim_floor_triggers_refine():
    """A single dimension below its floor forces refine even if composite is OK."""
    d = decide_next_step(
        current_node="judge",
        results={"judge": {
            "score": 4,
            "sub_scores": {"faithfulness": 2, "formatting": 5, "cohesion": 5, "hallucination_risk": 5},
        }},
        state_snapshot=_state(),
        config=_cfg(judge_dim_floors={"faithfulness": 3, "formatting": 0, "cohesion": 0, "hallucination_risk": 0}),
    )
    assert d.target == "refine"
    assert "faithfulness" in d.reason


def test_judge_dim_floor_zero_disabled():
    """Floor of 0 disables per-dimension gating for that dimension."""
    d = decide_next_step(
        current_node="judge",
        results={"judge": {
            "score": 4,
            "sub_scores": {"faithfulness": 1, "formatting": 5, "cohesion": 5, "hallucination_risk": 5},
        }},
        state_snapshot=_state(),
        config=_cfg(judge_dim_floors={"faithfulness": 0}),
    )
    assert d.target == "metadata"


def test_judge_dim_floor_all_above():
    """All dimensions above their floors → metadata."""
    d = decide_next_step(
        current_node="judge",
        results={"judge": {
            "score": 4,
            "sub_scores": {"faithfulness": 4, "formatting": 5, "cohesion": 4, "hallucination_risk": 5},
        }},
        state_snapshot=_state(),
        config=_cfg(judge_dim_floors={"faithfulness": 3, "hallucination_risk": 3}),
    )
    assert d.target == "metadata"


# ---------------------------------------------------------------------------
# REFINE routing
# ---------------------------------------------------------------------------

def test_refine_routes_to_judge():
    d = decide_next_step(current_node="refine", results={}, state_snapshot=_state(), config=_cfg())
    assert d.target == "judge"


# ---------------------------------------------------------------------------
# Linear tail
# ---------------------------------------------------------------------------

def test_metadata_to_embeddings():
    d = decide_next_step(current_node="metadata", results={}, state_snapshot=_state(), config=_cfg())
    assert d.target == "embeddings"


def test_embeddings_to_chunking():
    d = decide_next_step(current_node="embeddings", results={}, state_snapshot=_state(), config=_cfg())
    assert d.target == "chunking"


def test_chunking_to_commit():
    d = decide_next_step(current_node="chunking", results={}, state_snapshot=_state(), config=_cfg())
    assert d.target == "commit"


def test_commit_to_completed():
    d = decide_next_step(current_node="commit", results={}, state_snapshot=_state(), config=_cfg())
    assert d.target == "completed"


def test_hitl_to_completed():
    d = decide_next_step(current_node="hitl", results={}, state_snapshot=_state(), config=_cfg())
    assert d.target == "completed"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_unknown_node_returns_failed():
    d = decide_next_step(current_node="nonexistent", results={}, state_snapshot=_state(), config=_cfg())
    assert d.target == "failed"


def test_routing_decision_is_frozen():
    d = RoutingDecision(target="judge", reason="test")
    assert d.target == "judge"
    try:
        d.target = "metadata"  # type: ignore[misc]
        assert False, "Should be frozen"
    except AttributeError:
        pass
