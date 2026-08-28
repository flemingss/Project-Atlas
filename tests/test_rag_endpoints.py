from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from atlas import api_rag
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
    """Search must find ingested content — and only within its own scope.

    This test previously searched without ingesting anything and asserted the
    result was non-empty. It passed only because the fake store manufactured a
    hit when nothing matched, which made a scoping regression undetectable:
    a filter that excluded everything looked identical to a filter that worked.
    """
    app, _ = make_test_app(tmp_path, monkeypatch, include_admin=False)
    client = TestClient(app)

    ingested = client.post(
        "/rag/ingest/text",
        json={
            "doc_id": "scoped-doc",
            "doc_version": "1",
            "text": "The quick brown fox jumps over the lazy dog.",
            "tenant_id": "t-alpha",
            "project_id": "p-one",
            "corpus_id": "c-main",
        },
    )
    assert ingested.status_code == 200
    assert ingested.json()["chunks_upserted"] > 0

    in_scope = client.post(
        "/rag/search",
        json={
            "query": "fox",
            "top_k": 3,
            "tenant_id": "t-alpha",
            "project_id": "p-one",
            "corpus_id": "c-main",
        },
    )
    assert in_scope.status_code == 200
    body = in_scope.json()
    assert body["ok"] is True
    assert body["hits"], "ingested content must be findable in its own scope"
    assert body["hits"][0]["payload"]["doc_id"] == "scoped-doc"

    # The assertion that the manufactured hit made impossible: another
    # tenant must see nothing, not somebody else's document.
    other_tenant = client.post(
        "/rag/search",
        json={
            "query": "fox",
            "top_k": 3,
            "tenant_id": "t-beta",
            "project_id": "p-one",
            "corpus_id": "c-main",
        },
    )
    assert other_tenant.status_code == 200
    assert other_tenant.json()["hits"] == []

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


# ---------------------------------------------------------------------------
# 502 upstream-error redaction tests (P0-04)
# ---------------------------------------------------------------------------

# Exception text that mimics a leaky provider error: hostnames, provider
# internals, and config fragments that must never reach the client.
_LEAKY = "Connection refused: vllm.internal.host:9001 provider=lmstudio cfg=/etc/atlas/secret"


def test_ingest_text_502_does_not_leak_upstream_details(tmp_path: Path, monkeypatch: Any) -> None:
    """ingest/text 502 must return a generic message and log the exception."""
    app, _ = make_test_app(tmp_path, monkeypatch, include_admin=False)

    async def booming(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(_LEAKY)

    monkeypatch.setattr(api_rag, "ingest_text_via_pipeline", booming)
    client = TestClient(app, raise_server_exceptions=False)

    res = client.post(
        "/rag/ingest/text",
        json={"doc_id": "boom", "doc_version": "1", "text": "x"},
    )
    assert res.status_code == 502
    body = res.text
    # (a) exception text must NOT appear in the response
    assert _LEAKY not in body
    assert "vllm.internal.host" not in body
    # (b) stable generic message must appear
    assert res.json()["detail"] == api_rag._UPSTREAM_ERROR_DETAIL


def test_ingest_file_502_does_not_leak_upstream_details(tmp_path: Path, monkeypatch: Any) -> None:
    """ingest/file 502 must return a generic message and log the exception."""
    app, _ = make_test_app(tmp_path, monkeypatch, include_admin=False)

    async def booming(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(_LEAKY)

    monkeypatch.setattr(api_rag, "ingest_file_via_pipeline", booming)
    client = TestClient(app, raise_server_exceptions=False)

    res = client.post(
        "/rag/ingest/file",
        data={"doc_id": "boom", "doc_version": "1"},
        files={"file": ("x.pdf", b"fake-pdf-bytes", "application/pdf")},
    )
    assert res.status_code == 502
    body = res.text
    assert _LEAKY not in body
    assert "vllm.internal.host" not in body
    assert res.json()["detail"] == api_rag._UPSTREAM_ERROR_DETAIL


def test_search_502_does_not_leak_upstream_details(tmp_path: Path, monkeypatch: Any) -> None:
    """search 502 must return a generic message and log the exception."""
    app, _ = make_test_app(tmp_path, monkeypatch, include_admin=False)

    async def booming_embed(*, model: str, texts: list[str], params: dict[str, Any]) -> list[list[float]]:
        raise RuntimeError(_LEAKY)

    class BoomProvider:
        async def embed(self, *, model: str, texts: list[str], params: dict[str, Any]) -> list[list[float]]:
            return await booming_embed(model=model, texts=texts, params=params)

    class BoomRegistry:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def resolve(self, name: str) -> Any:
            class R:
                provider_name = "lmstudio"
                model_name = "emb"
                params: dict[str, Any] = {}

            return R()

        def provider_for(self, pname: str) -> Any:
            return BoomProvider()

    monkeypatch.setattr(api_rag, "ModelRegistry", BoomRegistry)
    client = TestClient(app, raise_server_exceptions=False)

    res = client.post("/rag/search", json={"query": "hello"})
    assert res.status_code == 502
    body = res.text
    assert _LEAKY not in body
    assert "vllm.internal.host" not in body
    assert res.json()["detail"] == api_rag._UPSTREAM_ERROR_DETAIL


def test_502_paths_log_full_exception_server_side(
    tmp_path: Path, monkeypatch: Any, caplog: Any
) -> None:
    """The upstream exception must be logged at error level with traceback."""
    records: list[logging.LogRecord] = []

    class _Capturing(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    capturing = _Capturing()
    logger = logging.getLogger(api_rag.__name__)
    logger.addHandler(capturing)
    try:
        app, _ = make_test_app(tmp_path, monkeypatch, include_admin=False)
        # ensure_schema → alembic parses alembic.ini via fileConfig, which
        # wipes root handlers AND disables pre-existing loggers. Attaching
        # our own handler to the module logger avoids both pitfalls.
        logger.disabled = False

        async def booming(**kwargs: Any) -> dict[str, Any]:
            raise RuntimeError(_LEAKY)

        monkeypatch.setattr(api_rag, "ingest_text_via_pipeline", booming)
        client = TestClient(app, raise_server_exceptions=False)

        client.post(
            "/rag/ingest/text",
            json={"doc_id": "boom", "doc_version": "1", "text": "x"},
        )
    finally:
        logger.removeHandler(capturing)

    matching = [r for r in records if r.levelno >= logging.ERROR]
    assert matching, "expected an error-level log record from atlas.api_rag"
    rec = matching[0]
    # traceback attached → operators can diagnose
    assert rec.exc_info is not None
