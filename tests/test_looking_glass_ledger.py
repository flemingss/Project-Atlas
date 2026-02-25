from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.helpers import make_test_app


def test_looking_glass_ledger_endpoints(tmp_path: Path, monkeypatch: Any) -> None:
    app, _ = make_test_app(tmp_path, monkeypatch, include_rag=False)
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

    # Richer summary fields
    assert s["workflow_runs"]["docs_unique"] == 2  # doc-ok and doc-bad
    assert s["workflow_runs"]["runs_last_24h"] == 2  # both were just created

    inflight = client.get("/admin/looking-glass/ledger/in-flight")
    assert inflight.status_code == 200
    inflight_runs = inflight.json()
    assert {r["id"] for r in inflight_runs} == {run_ok["id"]}

    # Test tenant filtering on in-flight
    inflight_t1 = client.get("/admin/looking-glass/ledger/in-flight", params={"tenant_id": "t1"})
    assert inflight_t1.status_code == 200
    assert len(inflight_t1.json()) == 1

    inflight_no_match = client.get("/admin/looking-glass/ledger/in-flight", params={"tenant_id": "other"})
    assert inflight_no_match.status_code == 200
    assert len(inflight_no_match.json()) == 0

    failures = client.get("/admin/looking-glass/ledger/failures")
    assert failures.status_code == 200
    payload = failures.json()
    assert payload["failures"][0]["run"]["id"] == run_failed["id"]
    assert payload["failures"][0]["node_errors"][0]["error_code"] == "E_REFINE"

    # Test tenant filtering on failures
    failures_t1 = client.get("/admin/looking-glass/ledger/failures", params={"tenant_id": "t1"})
    assert failures_t1.status_code == 200
    assert len(failures_t1.json()["failures"]) == 1

    failures_no_match = client.get("/admin/looking-glass/ledger/failures", params={"tenant_id": "other"})
    assert failures_no_match.status_code == 200
    assert len(failures_no_match.json()["failures"]) == 0

    hitl_pending = client.get("/admin/looking-glass/ledger/hitl")
    assert hitl_pending.status_code == 200
    tasks = hitl_pending.json()
    assert tasks[0]["chunk_id"] == "c2"  # higher priority
