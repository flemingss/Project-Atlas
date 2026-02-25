from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from atlas.artifacts import write_text
from atlas.workflow_ledger import ArtifactRefCreateRequest, WorkflowRunCreateRequest, add_artifact_ref, create_workflow_run
from tests.helpers import FakeQdrantStore, make_test_app


def _make_test_app(*, tmp_root: Path, monkeypatch: Any) -> tuple[Any, Any]:
    app, session_factory = make_test_app(tmp_root, monkeypatch, include_rag=False)
    return app, session_factory


def test_admin_set_active_doc_version_updates_qdrant_payload(tmp_path: Path, monkeypatch: Any) -> None:
    app, _ = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)
    client = TestClient(app)

    res = client.post("/admin/docs/demo/active-version", json={"doc_version": "v2"})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["active_doc_version"] == "v2"

    # Should set all inactive then selected active.
    assert len(FakeQdrantStore.last_set_payload_calls) == 2
    assert FakeQdrantStore.last_set_payload_calls[0]["payload"] == {"is_active_version": False}
    assert FakeQdrantStore.last_set_payload_calls[1]["payload"] == {"is_active_version": True}


def test_admin_export_doc_returns_zip_with_manifest_and_markdown(tmp_path: Path, monkeypatch: Any) -> None:
    app, session_factory = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)

    # Seed a completed run + markdown artifact.
    with session_factory() as session:
        run = create_workflow_run(
            session,
            req=WorkflowRunCreateRequest(
                tenant_id="local",
                project_id="default",
                doc_id="demo",
                doc_version="v1",
                status="completed",
                current_node="completed",
                meta={},
            ),
        )
        run_id = int(run.id)

    artifacts_dir = Path(tmp_path / "artifacts")
    md = write_text(
        artifacts_dir=artifacts_dir,
        rel_path=f"runs/{run_id}/ingest/markdown.md",
        text="# Title\n\nHello",
        mime_type="text/markdown",
    )
    with session_factory() as session:
        add_artifact_ref(
            session,
            run_id=run_id,
            req=ArtifactRefCreateRequest(
                kind="markdown_projection",
                path=md.rel_path,
                sha256=md.sha256,
                mime_type=md.mime_type,
                meta={},
            ),
        )

    client = TestClient(app)
    res = client.get("/admin/docs/demo/export")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/zip")

    z = zipfile.ZipFile(io.BytesIO(res.content))
    names = set(z.namelist())
    assert "manifest.json" in names
    assert "document.md" in names
    assert "index.json" in names

    manifest = json.loads(z.read("manifest.json").decode("utf-8"))
    assert manifest["doc_id"] == "demo"
    assert manifest["doc_version"] == "v1"

    doc_md = z.read("document.md").decode("utf-8")
    assert doc_md.startswith("---\n")
    assert "# Title" in doc_md
