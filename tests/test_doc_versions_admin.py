from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

import atlas.api_admin as api_admin
import atlas.export_package as export_package
from atlas.api_admin import make_admin_router
from atlas.artifacts import write_text
from atlas.config_manager import ConfigManager
from atlas.db import make_engine, make_sessionmaker
from atlas.db_init import ensure_schema
from atlas.workflow_ledger import ArtifactRefCreateRequest, WorkflowRunCreateRequest, add_artifact_ref, create_workflow_run


def _write_minimal_yaml_config(root_dir: Path) -> None:
    (root_dir / "config").mkdir(parents=True, exist_ok=True)
    (root_dir / "config" / "pipeline.yaml").write_text(
        "version: 1\nlimits: { chunk_max_chars: 1000 }\n",
        encoding="utf-8",
    )
    (root_dir / "config" / "models.yaml").write_text(
        "version: 1\nproviders: { deterministic: { type: deterministic } }\nroles: { embed_model: { provider: deterministic, model_name: deterministic-embed, params: { dim: 8 } } }\n",
        encoding="utf-8",
    )


class _FakeQdrantStore:
    last_set_payload_calls: list[dict[str, Any]] = []

    def __init__(self, *, url: str, api_key: str | None, collection: str):
        self._collection = collection

    @property
    def collection(self) -> str:
        return self._collection

    def set_payload(self, *, payload: dict[str, Any], must: list[Any]) -> None:
        _FakeQdrantStore.last_set_payload_calls.append({"payload": payload, "must": must})

    def scroll_points(self, *, must: list[Any], limit: int = 256, max_points: int = 10_000) -> list[Any]:
        # Minimal shape compatible with export_package helpers.
        return [
            {"id": "p1", "payload": {"chunk_index": 0, "content_hash": "h1", "text": "hello", "created_at": "t"}},
            {"id": "p2", "payload": {"chunk_index": 1, "content_hash": "h2", "text": "world", "created_at": "t"}},
        ]


def _make_test_app(*, tmp_root: Path, monkeypatch: Any) -> tuple[FastAPI, Any]:
    _write_minimal_yaml_config(tmp_root)
    config_manager = ConfigManager(root_dir=tmp_root)

    db_path = tmp_root / "test.sqlite"
    engine = make_engine(f"sqlite+pysqlite:///{db_path}")
    ensure_schema(engine)
    session_factory = make_sessionmaker(engine)

    # Export reads from Settings().atlas_artifacts_dir.
    artifacts_dir = tmp_root / "artifacts"
    monkeypatch.setenv("ATLAS_ARTIFACTS_DIR", str(artifacts_dir))

    # Mock Qdrant store in both admin module and export module.
    _FakeQdrantStore.last_set_payload_calls = []
    monkeypatch.setattr(api_admin, "QdrantStore", _FakeQdrantStore)
    monkeypatch.setattr(export_package, "QdrantStore", _FakeQdrantStore)

    app = FastAPI()
    app.include_router(make_admin_router(config_manager=config_manager, session_factory=session_factory))
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
    assert len(_FakeQdrantStore.last_set_payload_calls) == 2
    assert _FakeQdrantStore.last_set_payload_calls[0]["payload"] == {"is_active_version": False}
    assert _FakeQdrantStore.last_set_payload_calls[1]["payload"] == {"is_active_version": True}


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
