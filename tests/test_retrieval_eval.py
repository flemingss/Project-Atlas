from __future__ import annotations

from atlas.eval.retrieval_eval import EvalCase, EvalHit, evaluate_case


def test_evaluate_case_hit_at_k_and_mrr() -> None:
    case = EvalCase(case_id="c1", query="q", expected_doc_ids=["d2"])
    hits = [
        EvalHit(doc_id="d1", score=0.9, text=""),
        EvalHit(doc_id="d2", score=0.8, text=""),
        EvalHit(doc_id="d3", score=0.7, text=""),
    ]

    r = evaluate_case(case=case, hits=hits, top_k=3)
    assert r.hit_at_k is True
    assert r.best_rank == 2
    assert abs(r.mrr - 0.5) < 1e-9


def test_evaluate_case_miss() -> None:
    case = EvalCase(case_id="c1", query="q", expected_doc_ids=["dx"])
    hits = [EvalHit(doc_id="d1", score=0.9, text="")] * 5

    r = evaluate_case(case=case, hits=hits, top_k=3)
    assert r.hit_at_k is False
    assert r.best_rank is None
    assert r.mrr == 0.0
