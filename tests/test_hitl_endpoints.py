from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.api_admin import make_admin_router
from atlas.config_manager import ConfigManager
from atlas.db import make_engine, make_sessionmaker
from atlas.db_init import ensure_schema


def _write_minimal_yaml_config(root_dir: Path) -> None:
    (root_dir / "config").mkdir(parents=True, exist_ok=True)
    (root_dir / "config" / "pipeline.yaml").write_text(
        "version: 1\nthresholds: { judge_cutoff_refine: 5 }\nlimits: { chunk_max_chars: 1000 }\n",
        encoding="utf-8",
    )
    (root_dir / "config" / "models.yaml").write_text(
        "version: 1\nproviders: { lmstudio: { type: openai_compat } }\nroles: { embed_model: { provider: lmstudio, model_name: text-embedding, params: {} } }\n",
        encoding="utf-8",
    )


def _make_test_app(*, tmp_root: Path) -> FastAPI:
    _write_minimal_yaml_config(tmp_root)
    config_manager = ConfigManager(root_dir=tmp_root)

    db_path = tmp_root / "test.sqlite"
    engine = make_engine(f"sqlite+pysqlite:///{db_path}")
    ensure_schema(engine)
    session_factory = make_sessionmaker(engine)

    app = FastAPI()
    app.include_router(make_admin_router(config_manager=config_manager, session_factory=session_factory))
    return app


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


def test_hitl_task_lifecycle(tmp_path: Path) -> None:
    app = _make_test_app(tmp_root=tmp_path)
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


def test_hitl_create_requires_run(tmp_path: Path) -> None:
    app = _make_test_app(tmp_root=tmp_path)
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
