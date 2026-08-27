from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.helpers import FakeQdrantStore, make_test_app


def test_rag_ingest_text_upserts_uuid_ids(tmp_path: Path, monkeypatch: Any) -> None:
    app, _ = make_test_app(tmp_path, monkeypatch, include_admin=False)
    client = TestClient(app)

    res = client.post(
        "/rag/ingest/text",
        json={"doc_id": "demo", "doc_version": "v1", "text": "hello world"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["chunks_upserted"] == 1

    assert FakeQdrantStore.last_points
    pid = FakeQdrantStore.last_points[0].id
    uuid.UUID(str(pid))

    payload0 = FakeQdrantStore.last_points[0].payload
    assert payload0.get("corpus_id") == "default"


def test_rag_search_returns_hits_and_applies_filters(tmp_path: Path, monkeypatch: Any) -> None:
    app, _ = make_test_app(tmp_path, monkeypatch, include_admin=False)
    client = TestClient(app)

    res = client.post("/rag/search", json={"query": "hello", "top_k": 3})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["hits"]

    # Sanity check: we pass tenant/project/finalized + fidelity filter to the store.
    assert len(FakeQdrantStore.last_search_must) == 6


# ---------------------------------------------------------------------------
# Phase 4 — Negative / error-path tests
# ---------------------------------------------------------------------------

def test_ingest_text_empty_body_returns_error(tmp_path: Path, monkeypatch: Any) -> None:
    """POST /rag/ingest/text with text='' produces no chunks (pipeline short-circuits)."""
    app, _ = make_test_app(tmp_path, monkeypatch, include_admin=False)
    client = TestClient(app)

    res = client.post(
        "/rag/ingest/text",
        json={"doc_id": "d1", "doc_version": "v1", "text": ""},
    )
    assert res.status_code == 200
    assert res.json()["chunks_upserted"] == 0


def test_ingest_text_missing_doc_id_returns_422(tmp_path: Path, monkeypatch: Any) -> None:
    """Omitting doc_id from the JSON body should return 422."""
    app, _ = make_test_app(tmp_path, monkeypatch, include_admin=False)
    client = TestClient(app)

    res = client.post(
        "/rag/ingest/text",
        json={"doc_version": "v1", "text": "hello"},
    )
    assert res.status_code == 422


def test_search_missing_query_returns_422(tmp_path: Path, monkeypatch: Any) -> None:
    """POST /rag/search with no query field should return 422."""
    app, _ = make_test_app(tmp_path, monkeypatch, include_admin=False)
    client = TestClient(app)

    res = client.post("/rag/search", json={"top_k": 3})
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# Fidelity-aware search filter tests
# ---------------------------------------------------------------------------

def test_search_fidelity_default_is_verified(tmp_path: Path, monkeypatch: Any) -> None:
    """Default fidelity_mode=verified adds a fidelity_flag filter."""
    app, _ = make_test_app(tmp_path, monkeypatch, include_admin=False)
    client = TestClient(app)

    res = client.post("/rag/search", json={"query": "hello"})
    assert res.status_code == 200
    # 5 standard filters + 1 fidelity_flag == "verified"
    assert len(FakeQdrantStore.last_search_must) == 6
    fidelity_cond = FakeQdrantStore.last_search_must[-1]
    assert fidelity_cond.key == "fidelity_flag"
    assert getattr(fidelity_cond.match, "value", None) == "verified"


def test_search_fidelity_all_no_extra_filter(tmp_path: Path, monkeypatch: Any) -> None:
    """fidelity_mode=all should NOT add a fidelity_flag condition."""
    app, _ = make_test_app(tmp_path, monkeypatch, include_admin=False)
    client = TestClient(app)

    res = client.post("/rag/search", json={"query": "hello", "fidelity_mode": "all"})
    assert res.status_code == 200
    assert len(FakeQdrantStore.last_search_must) == 5  # no fidelity filter


def test_search_fidelity_verified_partial(tmp_path: Path, monkeypatch: Any) -> None:
    """fidelity_mode=verified+partial uses MatchAny."""
    app, _ = make_test_app(tmp_path, monkeypatch, include_admin=False)
    client = TestClient(app)

    res = client.post("/rag/search", json={"query": "hello", "fidelity_mode": "verified+partial"})
    assert res.status_code == 200
    assert len(FakeQdrantStore.last_search_must) == 6
    fidelity_cond = FakeQdrantStore.last_search_must[-1]
    assert fidelity_cond.key == "fidelity_flag"
    any_vals = getattr(fidelity_cond.match, "any", None)
    assert set(any_vals) == {"verified", "partial"}
