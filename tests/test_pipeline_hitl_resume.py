from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.helpers import FakeQdrantStore, make_test_app


def test_pipeline_hitl_resume_commits_chunks(tmp_path: Path, monkeypatch: Any) -> None:
    app, _ = make_test_app(tmp_path, monkeypatch)
    client = TestClient(app)

    ingest = client.post(
        "/rag/ingest/text",
        json={
            "doc_id": "doc1",
            "doc_version": "1",
            "text": "[UNFIXABLE]\n\n\uFFFD\uFFFD\uFFFD unreadable",
            "tenant_id": "t1",
            "project_id": "p1",
            "is_finalized": True,
            "is_sensitive": True,
            "source_mime_type": "text/plain",
            "metadata": {"source": "unit"},
        },
    )
    assert ingest.status_code == 200
    assert ingest.json()["chunks_upserted"] == 0

    nxt = client.post("/admin/hitl/tasks/next", params={"assigned_to": "op"})
    assert nxt.status_code == 200
    task = nxt.json()

    complete = client.post(
        f"/admin/hitl/tasks/{task['id']}/complete",
        json={"after_md": "# Overview\n\nFixed.", "reason_for_edit": "unit"},
    )
    assert complete.status_code == 200

    resume = client.post(f"/admin/hitl/tasks/{task['id']}/resume")
    assert resume.status_code == 200
    data = resume.json()
    assert data["ok"] is True
    assert data["chunks_upserted"] >= 1
    assert FakeQdrantStore.last_points

    # Verify fidelity_flag is present in the committed chunk payloads.
    for pt in FakeQdrantStore.last_points:
        assert "fidelity_flag" in pt.payload, "fidelity_flag must be wired into chunk payloads"


def test_pipeline_hitl_resume_double_resume_rejected(tmp_path: Path, monkeypatch: Any) -> None:
    """A second resume call on an already-completed run must be rejected with 409."""
    app, _ = make_test_app(tmp_path, monkeypatch)
    client = TestClient(app)

    ingest = client.post(
        "/rag/ingest/text",
        json={
            "doc_id": "doc-double",
            "doc_version": "1",
            "text": "[UNFIXABLE]\n\n\uFFFD\uFFFD\uFFFD unreadable",
            "tenant_id": "t1",
            "project_id": "p1",
            "is_finalized": True,
            "is_sensitive": False,
            "source_mime_type": "text/plain",
            "metadata": {},
        },
    )
    assert ingest.status_code == 200
    assert ingest.json()["chunks_upserted"] == 0

    nxt = client.post("/admin/hitl/tasks/next", params={"assigned_to": "op2"})
    assert nxt.status_code == 200
    task = nxt.json()

    client.post(
        f"/admin/hitl/tasks/{task['id']}/complete",
        json={"after_md": "# Fixed\n\nContent.", "reason_for_edit": "double"},
    )

    first_resume = client.post(f"/admin/hitl/tasks/{task['id']}/resume")
    assert first_resume.status_code == 200

    # Second resume on the now-completed run must return 409.
    second_resume = client.post(f"/admin/hitl/tasks/{task['id']}/resume")
    assert second_resume.status_code == 409
