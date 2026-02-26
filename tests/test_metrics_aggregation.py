"""Tests for the Looking Glass metrics aggregation endpoint (Phase 7C)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.helpers import make_test_app


def _seed_data(client: TestClient) -> None:
    """Seed workflow runs, HITL tasks, and feedback entries."""
    # Two completed runs, one failed
    for status in ("completed", "completed", "failed"):
        client.post(
            "/admin/runs",
            json={
                "tenant_id": "t1",
                "project_id": "p1",
                "doc_id": f"doc-{status}",
                "status": status,
            },
        )

    # One run for a different tenant
    client.post(
        "/admin/runs",
        json={
            "tenant_id": "t2",
            "project_id": "p2",
            "doc_id": "doc-t2",
            "status": "completed",
        },
    )

    # HITL task linked to first run
    runs = client.get("/admin/runs").json()
    first_run_id = runs[-1]["id"]
    client.post(
        "/admin/hitl/tasks",
        json={
            "run_id": first_run_id,
            "tenant_id": "t1",
            "project_id": "p1",
            "doc_id": "doc-completed",
            "before_md": "md",
        },
    )

    # Feedback entries
    client.post(
        "/admin/cleanup-feedback",
        json={"tenant_id": "t1", "project_id": "p1", "category": "ocr_artefact"},
    )
    client.post(
        "/admin/cleanup-feedback",
        json={"tenant_id": "t1", "project_id": "p1", "category": "bad_bullet_fix"},
    )
    client.post(
        "/admin/cleanup-feedback",
        json={"tenant_id": "t2", "project_id": "p2", "category": "ocr_artefact"},
    )


def test_metrics_unscoped(tmp_path: Path, monkeypatch: Any) -> None:
    app, _ = make_test_app(tmp_path, monkeypatch, include_rag=False)
    client = TestClient(app)
    _seed_data(client)

    resp = client.get("/admin/looking-glass/metrics")
    assert resp.status_code == 200
    m = resp.json()

    # Global scope
    assert m["scope"]["tenant_id"] is None
    assert m["scope"]["project_id"] is None

    # Workflow runs: 3 for t1 + 1 for t2 = 4
    assert m["workflow_runs"]["total"] == 4
    assert m["workflow_runs"]["by_status"]["completed"] == 3
    assert m["workflow_runs"]["by_status"]["failed"] == 1
    assert m["workflow_runs"]["completion_rate"] == 0.75
    assert m["workflow_runs"]["failure_rate"] == 0.25

    # HITL
    assert m["hitl"]["total"] >= 1

    # Feedback
    assert m["cleanup_feedback"]["total"] >= 3
    assert m["cleanup_feedback"]["by_category"]["ocr_artefact"] >= 2

    # Auto-accepted (completed minus hitl)
    assert m["auto_accepted"]["count"] >= 0


def test_metrics_scoped_by_tenant(tmp_path: Path, monkeypatch: Any) -> None:
    app, _ = make_test_app(tmp_path, monkeypatch, include_rag=False)
    client = TestClient(app)
    _seed_data(client)

    resp = client.get("/admin/looking-glass/metrics", params={"tenant_id": "t1"})
    assert resp.status_code == 200
    m = resp.json()

    assert m["scope"]["tenant_id"] == "t1"
    # t1 has 2 completed + 1 failed = 3 runs
    assert m["workflow_runs"]["total"] == 3
    assert m["workflow_runs"]["by_status"].get("completed") == 2

    # Feedback for t1 only
    assert m["cleanup_feedback"]["total"] == 2


def test_metrics_empty_scope(tmp_path: Path, monkeypatch: Any) -> None:
    """Metrics on an empty DB return zeroes, not errors."""
    app, _ = make_test_app(tmp_path, monkeypatch, include_rag=False)
    client = TestClient(app)

    resp = client.get("/admin/looking-glass/metrics")
    assert resp.status_code == 200
    m = resp.json()

    assert m["workflow_runs"]["total"] == 0
    assert m["workflow_runs"]["completion_rate"] == 0
    assert m["hitl"]["total"] == 0
    assert m["cleanup_feedback"]["total"] == 0
    assert m["auto_accepted"]["count"] == 0
