"""Commit streams in bounded windows rather than materialising the document.

The commit path used to embed every chunk, build every point, and only then
issue a single upsert — so peak memory scaled with document size. At the
2,000-3,000 page target that is gigabytes of float vectors resident before the
first byte reaches Qdrant, and any failure discarded the whole run's embedding
spend.

These tests pin the windowing itself, since a regression here is invisible on
small documents: the single-window case behaves identically either way.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from atlas.pipeline import runner
from tests.helpers import FakeQdrantStore, make_test_app

# Comfortably more than one chunk at the test config's 1000-char limit.
_LONG_TEXT = "\n\n".join(
    f"## Section {i}\n\n" + ("Sensor calibration procedure detail. " * 40)
    for i in range(6)
)


def test_commit_upserts_in_multiple_windows(tmp_path: Path, monkeypatch: Any) -> None:
    """More chunks than the window size must produce more than one round-trip."""
    monkeypatch.setattr(runner, "COMMIT_WINDOW_CHUNKS", 1)

    app, _ = make_test_app(tmp_path, monkeypatch, include_admin=False)
    client = TestClient(app)

    res = client.post(
        "/rag/ingest/text",
        json={"doc_id": "streamed", "doc_version": "1", "text": _LONG_TEXT},
    )
    assert res.status_code == 200
    upserted = res.json()["chunks_upserted"]
    assert upserted > 1, "test text must produce several chunks to be meaningful"

    # One call per chunk at a window of 1 — proof the loop is actually windowing
    # rather than accumulating everything and upserting once.
    assert FakeQdrantStore.upsert_calls == upserted
    assert FakeQdrantStore.upsert_count == upserted


def test_windowing_does_not_change_what_is_stored(tmp_path: Path, monkeypatch: Any) -> None:
    """A windowed commit and a single-window commit must agree exactly."""
    monkeypatch.setattr(runner, "COMMIT_WINDOW_CHUNKS", 1)
    app_a, _ = make_test_app(tmp_path / "a", monkeypatch, include_admin=False)
    res_a = TestClient(app_a).post(
        "/rag/ingest/text",
        json={"doc_id": "same-doc", "doc_version": "1", "text": _LONG_TEXT},
    )
    assert res_a.status_code == 200
    windowed = {
        p["id"]: p["payload"]["text"] for p in FakeQdrantStore._storage.values()
    }

    monkeypatch.setattr(runner, "COMMIT_WINDOW_CHUNKS", 10_000)
    app_b, _ = make_test_app(tmp_path / "b", monkeypatch, include_admin=False)
    res_b = TestClient(app_b).post(
        "/rag/ingest/text",
        json={"doc_id": "same-doc", "doc_version": "1", "text": _LONG_TEXT},
    )
    assert res_b.status_code == 200
    single = {p["id"]: p["payload"]["text"] for p in FakeQdrantStore._storage.values()}

    assert res_a.json()["chunks_upserted"] == res_b.json()["chunks_upserted"]
    # Chunk ids are deterministic, so identical input must land identically
    # however it was batched.
    assert windowed == single


def test_stale_purge_still_precedes_the_first_upsert(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The purge must not delete the rows the same commit just wrote.

    Purging moved inside the loop when commit began streaming; running it on
    any window but the first would wipe earlier windows of the same document.
    """
    monkeypatch.setattr(runner, "COMMIT_WINDOW_CHUNKS", 1)
    app, _ = make_test_app(tmp_path, monkeypatch, include_admin=False)
    client = TestClient(app)

    first = client.post(
        "/rag/ingest/text",
        json={"doc_id": "revised", "doc_version": "1", "text": _LONG_TEXT},
    )
    assert first.status_code == 200
    expected = first.json()["chunks_upserted"]

    # Every chunk of the multi-window run must survive to the end.
    assert len(FakeQdrantStore._storage) == expected

    # Re-ingesting the same doc_id/version replaces rather than duplicates.
    again = client.post(
        "/rag/ingest/text",
        json={"doc_id": "revised", "doc_version": "1", "text": _LONG_TEXT},
    )
    assert again.status_code == 200
    assert len(FakeQdrantStore._storage) == expected
