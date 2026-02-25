from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.helpers import make_test_app


def test_workflow_run_crud(tmp_path: Path, monkeypatch: Any) -> None:
    app, _ = make_test_app(tmp_path, monkeypatch, include_rag=False)
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


def test_workflow_run_404(tmp_path: Path, monkeypatch: Any) -> None:
    app, _ = make_test_app(tmp_path, monkeypatch, include_rag=False)
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
