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


def test_workflow_run_crud(tmp_path: Path) -> None:
    app = _make_test_app(tmp_root=tmp_path)
    client = TestClient(app)

    created = client.post(
        "/admin/runs",
        json={
            "tenant_id": "t1",
            "project_id": "p1",
            "doc_id": "doc-123",
            "doc_version": "1",
            "status": "pending",
            "current_node": "ingest",
            "meta": {"source": "unit"},
        },
    )
    assert created.status_code == 200
    run = created.json()
    assert isinstance(run["id"], int)
    assert run["tenant_id"] == "t1"
    assert run["doc_id"] == "doc-123"

    listed = client.get("/admin/runs").json()
    assert any(r["id"] == run["id"] for r in listed)

    detail = client.get(f"/admin/runs/{run['id']}")
    assert detail.status_code == 200
    assert detail.json()["doc_id"] == "doc-123"

    node_created = client.post(
        f"/admin/runs/{run['id']}/node-runs",
        json={"node_name": "ingest", "status": "running", "input_ref": "in.json", "output_ref": "out.json"},
    )
    assert node_created.status_code == 200
    node = node_created.json()
    assert node["run_id"] == run["id"]
    assert node["node_name"] == "ingest"

    nodes = client.get(f"/admin/runs/{run['id']}/node-runs").json()
    assert len(nodes) == 1
    assert nodes[0]["id"] == node["id"]

    art_created = client.post(
        f"/admin/runs/{run['id']}/artifacts",
        json={
            "kind": "docling_json",
            "path": "artifacts/doc-123/docling.json",
            "node_run_id": node["id"],
            "mime_type": "application/json",
            "meta": {"note": "test"},
        },
    )
    assert art_created.status_code == 200
    art = art_created.json()
    assert art["run_id"] == run["id"]
    assert art["kind"] == "docling_json"

    arts = client.get(f"/admin/runs/{run['id']}/artifacts").json()
    assert len(arts) == 1
    assert arts[0]["id"] == art["id"]


def test_workflow_run_404(tmp_path: Path) -> None:
    app = _make_test_app(tmp_root=tmp_path)
    client = TestClient(app)

    res = client.get("/admin/runs/999999")
    assert res.status_code == 404

    res2 = client.post("/admin/runs/999999/node-runs", json={"node_name": "ingest"})
    assert res2.status_code == 404

    res3 = client.post(
        "/admin/runs/999999/artifacts",
        json={"kind": "x", "path": "y"},
    )
    assert res3.status_code == 404
