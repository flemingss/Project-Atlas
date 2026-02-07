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


def test_looking_glass_ledger_endpoints(tmp_path: Path) -> None:
    app = _make_test_app(tmp_root=tmp_path)
    client = TestClient(app)

    run_ok = client.post(
        "/admin/runs",
        json={
            "tenant_id": "t1",
            "project_id": "p1",
            "doc_id": "doc-ok",
            "doc_version": "1",
            "status": "running",
            "current_node": "judge",
        },
    ).json()

    run_failed_res = client.post(
        "/admin/runs",
        json={
            "tenant_id": "t1",
            "project_id": "p1",
            "doc_id": "doc-bad",
            "doc_version": "1",
            "status": "failed",
            "current_node": "refine",
        },
    )
    assert run_failed_res.status_code == 200
    run_failed = run_failed_res.json()

    node_res = client.post(
        f"/admin/runs/{run_failed['id']}/node-runs",
        json={
            "node_name": "refine",
            "status": "failed",
            "error_code": "E_REFINE",
            "error_message": "boom",
        },
    )
    assert node_res.status_code == 200

    # Create a couple HITL tasks so the HITL view is non-empty
    t1 = client.post(
        "/admin/hitl/tasks",
        json={
            "run_id": run_ok["id"],
            "tenant_id": "t1",
            "project_id": "p1",
            "doc_id": "doc-ok",
            "doc_version": "1",
            "chunk_id": "c1",
            "is_sensitive": False,
            "judge_score": 9.0,
            "before_md": "x",
        },
    )
    assert t1.status_code == 200

    t2 = client.post(
        "/admin/hitl/tasks",
        json={
            "run_id": run_failed["id"],
            "tenant_id": "t1",
            "project_id": "p1",
            "doc_id": "doc-bad",
            "doc_version": "1",
            "chunk_id": "c2",
            "is_sensitive": True,
            "judge_score": 2.0,
            "before_md": "y",
        },
    )
    assert t2.status_code == 200

    summary = client.get("/admin/looking-glass/ledger/summary")
    assert summary.status_code == 200
    s = summary.json()
    assert s["workflow_runs"]["by_status"]["running"] == 1
    assert s["workflow_runs"]["by_status"]["failed"] == 1
    assert s["node_runs"]["failed_by_error_code"]["E_REFINE"] == 1
    assert s["hitl_tasks"]["by_status"]["pending"] == 2

    inflight = client.get("/admin/looking-glass/ledger/in-flight")
    assert inflight.status_code == 200
    inflight_runs = inflight.json()
    assert {r["id"] for r in inflight_runs} == {run_ok["id"]}

    failures = client.get("/admin/looking-glass/ledger/failures")
    assert failures.status_code == 200
    payload = failures.json()
    assert payload["failures"][0]["run"]["id"] == run_failed["id"]
    assert payload["failures"][0]["node_errors"][0]["error_code"] == "E_REFINE"

    hitl_pending = client.get("/admin/looking-glass/ledger/hitl")
    assert hitl_pending.status_code == 200
    tasks = hitl_pending.json()
    assert tasks[0]["chunk_id"] == "c2"  # higher priority
