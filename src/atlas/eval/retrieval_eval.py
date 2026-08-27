from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    query: str
    expected_doc_ids: list[str]
    notes: str = ""


@dataclass(frozen=True)
class EvalHit:
    doc_id: str
    score: float
    text: str


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    ok: bool
    hit_at_k: bool
    mrr: float
    best_rank: int | None


def _first_match_rank(hits: Iterable[EvalHit], expected_doc_ids: set[str]) -> int | None:
    for idx, h in enumerate(hits, start=1):
        if h.doc_id in expected_doc_ids:
            return idx
    return None


def evaluate_case(*, case: EvalCase, hits: list[EvalHit], top_k: int) -> CaseResult:
    expected = {str(x) for x in (case.expected_doc_ids or [])}
    hits_k = hits[: int(top_k)]

    rank = _first_match_rank(hits_k, expected)
    hit_at_k = rank is not None
    mrr = 0.0 if rank is None else 1.0 / float(rank)

    # For this initial gate harness, "ok" means we hit an expected doc in top_k.
    return CaseResult(case_id=case.case_id, ok=bool(hit_at_k), hit_at_k=bool(hit_at_k), mrr=float(mrr), best_rank=rank)


def evaluate(
    *,
    cases: list[EvalCase],
    search_fn: Callable[[str], list[EvalHit]],
    top_k: int,
) -> dict[str, Any]:
    results: list[CaseResult] = []
    for c in cases:
        hits = search_fn(c.query)
        results.append(evaluate_case(case=c, hits=hits, top_k=int(top_k)))

    hit_rate = 0.0
    mrr = 0.0
    if results:
        hit_rate = sum(1 for r in results if r.hit_at_k) / float(len(results))
        mrr = sum(r.mrr for r in results) / float(len(results))

    return {
        "schema_version": 1,
        "top_k": int(top_k),
        "summary": {
            "cases": len(results),
            "hit_rate": hit_rate,
            "mrr": mrr,
        },
        "results": [
            {
                "case_id": r.case_id,
                "ok": r.ok,
                "hit_at_k": r.hit_at_k,
                "mrr": r.mrr,
                "best_rank": r.best_rank,
            }
            for r in results
        ],
    }


def load_golden_set(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    obj = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("golden set must be a JSON object")
    return obj


def parse_cases(obj: dict[str, Any]) -> tuple[list[EvalCase], int, str | None, str | None]:
    top_k = int(obj.get("top_k", 10))
    tenant_id = obj.get("tenant_id")
    project_id = obj.get("project_id")

    raw_cases = obj.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("golden set must contain non-empty 'cases' list")

    cases: list[EvalCase] = []
    for i, rc in enumerate(raw_cases):
        if not isinstance(rc, dict):
            raise ValueError("each case must be an object")
        case_id = str(rc.get("id") or f"case_{i}")
        query = str(rc.get("query") or "").strip()
        expected = rc.get("expected_doc_ids")
        if not query:
            raise ValueError(f"case '{case_id}' missing query")
        if not isinstance(expected, list) or not expected:
            raise ValueError(f"case '{case_id}' missing expected_doc_ids")
        cases.append(
            EvalCase(
                case_id=case_id,
                query=query,
                expected_doc_ids=[str(x) for x in expected],
                notes=str(rc.get("notes") or ""),
            )
        )

    return cases, top_k, (None if tenant_id is None else str(tenant_id)), (None if project_id is None else str(project_id))


def http_search_fn(*, api_url: str, tenant_id: str | None, project_id: str | None, top_k: int) -> Callable[[str], list[EvalHit]]:
    base = api_url.rstrip("/")

    def _search(query: str) -> list[EvalHit]:
        payload: dict[str, Any] = {"query": query, "top_k": int(top_k)}
        if tenant_id is not None:
            payload["tenant_id"] = tenant_id
        if project_id is not None:
            payload["project_id"] = project_id

        with httpx.Client(timeout=30.0) as client:
            r = client.post(f"{base}/rag/search", json=payload)
            r.raise_for_status()
            data = r.json()

        hits: list[EvalHit] = []
        for h in (data.get("hits") or []):
            if not isinstance(h, dict):
                continue
            hits.append(
                EvalHit(
                    doc_id=str(h.get("doc_id") or ""),
                    score=float(h.get("score") or 0.0),
                    text=str(h.get("text") or ""),
                )
            )
        return hits

    return _search
