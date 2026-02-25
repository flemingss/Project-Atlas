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

    # Sanity check: we pass tenant/project/finalized filter to the store.
    assert len(FakeQdrantStore.last_search_must) == 5


# ---------------------------------------------------------------------------
# Phase 4 — Negative / error-path tests
# ---------------------------------------------------------------------------

def test_ingest_text_empty_body_returns_error(tmp_path: Path, monkeypatch: Any) -> None:
    """POST /rag/ingest/text with text='' should return a 422 or produce no chunks."""
    app, _ = make_test_app(tmp_path, monkeypatch, include_admin=False)
    client = TestClient(app)

    res = client.post(
        "/rag/ingest/text",
        json={"doc_id": "d1", "doc_version": "v1", "text": ""},
    )
    # Either a validation error (422) or a 200 with zero chunks upserted is acceptable.
    assert res.status_code in (200, 422)
    if res.status_code == 200:
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
