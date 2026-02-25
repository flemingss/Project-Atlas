from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.helpers import make_test_app


def _create_run(client: TestClient) -> dict:
    res = client.post(
        "/admin/runs",
        json={
            "tenant_id": "t1",
            "project_id": "p1",
            "doc_id": "doc-1",
            "doc_version": "1",
            "status": "pending",
            "current_node": "hitl",
        },
    )
    assert res.status_code == 200
    return res.json()


def test_hitl_task_lifecycle(tmp_path: Path, monkeypatch: Any) -> None:
    app, _ = make_test_app(tmp_path, monkeypatch, include_rag=False)
    client = TestClient(app)

    run = _create_run(client)

    # Create two tasks with different priority; ensure claim_next returns highest priority.
    low_priority = client.post(
        "/admin/hitl/tasks",
        json={
            "run_id": run["id"],
            "tenant_id": "t1",
            "project_id": "p1",
            "doc_id": "doc-1",
            "doc_version": "1",
            "chunk_id": "c-low",
            "is_sensitive": False,
            "judge_score": 4.5,
            "before_md": "before low",
        },
    )
    assert low_priority.status_code == 200

    high_priority = client.post(
        "/admin/hitl/tasks",
        json={
            "run_id": run["id"],
            "tenant_id": "t1",
            "project_id": "p1",
            "doc_id": "doc-1",
            "doc_version": "1",
            "chunk_id": "c-high",
            "is_sensitive": True,
            "judge_score": 2.0,
            "before_md": "before high",
        },
    )
    assert high_priority.status_code == 200

    nxt = client.post("/admin/hitl/tasks/next", params={"assigned_to": "op"})
    assert nxt.status_code == 200
    next_task = nxt.json()
    assert next_task["chunk_id"] == "c-high"
    assert next_task["status"] == "in_progress"
    assert next_task["assigned_to"] == "op"

    # Completing a pending task should 409
    pending_tasks = client.get("/admin/hitl/tasks", params={"status": "pending"}).json()
    assert len(pending_tasks) == 1
    pending_id = pending_tasks[0]["id"]
    bad_complete = client.post(
        f"/admin/hitl/tasks/{pending_id}/complete",
        json={"after_md": "x", "reason_for_edit": "y"},
    )
    assert bad_complete.status_code == 409

    # Complete claimed task
    completed = client.post(
        f"/admin/hitl/tasks/{next_task['id']}/complete",
        json={"after_md": "fixed", "reason_for_edit": "typo"},
    )
    assert completed.status_code == 200
    data = completed.json()
    assert data["status"] == "completed"
    assert data["after_md"] == "fixed"

    # Skip remaining pending
    skipped = client.post(
        f"/admin/hitl/tasks/{pending_id}/skip",
        json={"reason": "not needed"},
    )
    assert skipped.status_code == 200
    assert skipped.json()["status"] == "skipped"


def test_hitl_create_requires_run(tmp_path: Path, monkeypatch: Any) -> None:
    app, _ = make_test_app(tmp_path, monkeypatch, include_rag=False)
    client = TestClient(app)

    res = client.post(
        "/admin/hitl/tasks",
        json={
            "run_id": 999999,
            "tenant_id": "t1",
            "project_id": "p1",
            "doc_id": "doc-1",
            "doc_version": "1",
            "before_md": "x",
        },
    )
    assert res.status_code == 404
