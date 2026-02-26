"""Tests for cleanup-feedback ledger and admin API endpoints (Phase 7B)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.helpers import make_test_app


def test_feedback_crud_lifecycle(tmp_path: Path, monkeypatch: Any) -> None:
    """Create → list → get → delete lifecycle."""
    app, _ = make_test_app(tmp_path, monkeypatch, include_rag=False)
    client = TestClient(app)

    # Create feedback entry
    create_resp = client.post(
        "/admin/cleanup-feedback",
        json={
            "tenant_id": "t1",
            "project_id": "p1",
            "corpus_id": "c1",
            "doc_id": "doc-123",
            "category": "ocr_artefact",
            "description": "Random whitespace in paragraph 3",
            "created_by": "tester",
        },
    )
    assert create_resp.status_code == 201
    data = create_resp.json()
    fb_id = data["id"]
    assert data["tenant_id"] == "t1"
    assert data["category"] == "ocr_artefact"
    assert data["created_by"] == "tester"

    # Get by ID
    get_resp = client.get(f"/admin/cleanup-feedback/{fb_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == fb_id

    # List (no filter)
    list_resp = client.get("/admin/cleanup-feedback")
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert len(items) >= 1
    assert any(i["id"] == fb_id for i in items)

    # List by category
    list_cat = client.get("/admin/cleanup-feedback", params={"category": "ocr_artefact"})
    assert list_cat.status_code == 200
    assert len(list_cat.json()) >= 1

    list_cat_miss = client.get("/admin/cleanup-feedback", params={"category": "nonexistent"})
    assert list_cat_miss.status_code == 200
    assert len(list_cat_miss.json()) == 0

    # Delete
    del_resp = client.delete(f"/admin/cleanup-feedback/{fb_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True

    # Confirm deleted
    get_gone = client.get(f"/admin/cleanup-feedback/{fb_id}")
    assert get_gone.status_code == 404


def test_feedback_get_not_found(tmp_path: Path, monkeypatch: Any) -> None:
    app, _ = make_test_app(tmp_path, monkeypatch, include_rag=False)
    client = TestClient(app)
    resp = client.get("/admin/cleanup-feedback/99999")
    assert resp.status_code == 404


def test_feedback_delete_not_found(tmp_path: Path, monkeypatch: Any) -> None:
    app, _ = make_test_app(tmp_path, monkeypatch, include_rag=False)
    client = TestClient(app)
    resp = client.delete("/admin/cleanup-feedback/99999")
    assert resp.status_code == 404


def test_feedback_list_scoped(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify tenant/project/corpus scoping on list."""
    app, _ = make_test_app(tmp_path, monkeypatch, include_rag=False)
    client = TestClient(app)

    # Create two entries in different tenants
    client.post(
        "/admin/cleanup-feedback",
        json={"tenant_id": "t1", "doc_id": "d1", "category": "a"},
    )
    client.post(
        "/admin/cleanup-feedback",
        json={"tenant_id": "t2", "doc_id": "d2", "category": "b"},
    )

    all_items = client.get("/admin/cleanup-feedback").json()
    assert len(all_items) >= 2

    t1_items = client.get("/admin/cleanup-feedback", params={"tenant_id": "t1"}).json()
    assert all(i["tenant_id"] == "t1" for i in t1_items)
    assert len(t1_items) >= 1


def test_feedback_category_counts(tmp_path: Path, monkeypatch: Any) -> None:
    app, _ = make_test_app(tmp_path, monkeypatch, include_rag=False)
    client = TestClient(app)

    client.post("/admin/cleanup-feedback", json={"category": "ocr_artefact"})
    client.post("/admin/cleanup-feedback", json={"category": "ocr_artefact"})
    client.post("/admin/cleanup-feedback", json={"category": "bad_bullet_fix"})

    resp = client.get("/admin/cleanup-feedback/categories")
    assert resp.status_code == 200
    counts = resp.json()
    assert counts.get("ocr_artefact") == 2
    assert counts.get("bad_bullet_fix") == 1


def test_feedback_with_run_id(tmp_path: Path, monkeypatch: Any) -> None:
    """Feedback can link to a workflow run."""
    app, _ = make_test_app(tmp_path, monkeypatch, include_rag=False)
    client = TestClient(app)

    # Create a workflow run first
    run_resp = client.post(
        "/admin/runs",
        json={
            "tenant_id": "t1",
            "project_id": "p1",
            "doc_id": "doc-1",
            "status": "completed",
        },
    )
    run_id = run_resp.json()["id"]

    fb_resp = client.post(
        "/admin/cleanup-feedback",
        json={
            "tenant_id": "t1",
            "doc_id": "doc-1",
            "run_id": run_id,
            "category": "missed_header_strip",
            "description": "Header line not removed",
        },
    )
    assert fb_resp.status_code == 201
    assert fb_resp.json()["run_id"] == run_id


def test_feedback_with_source_span(tmp_path: Path, monkeypatch: Any) -> None:
    app, _ = make_test_app(tmp_path, monkeypatch, include_rag=False)
    client = TestClient(app)

    fb = client.post(
        "/admin/cleanup-feedback",
        json={
            "doc_id": "doc-2",
            "category": "ocr_artefact",
            "source_span_start": 100,
            "source_span_end": 150,
        },
    )
    assert fb.status_code == 201
    data = fb.json()
    assert data["source_span_start"] == 100
    assert data["source_span_end"] == 150
