from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.api_admin import make_admin_router
from atlas.api_rag import make_rag_router
from atlas.config_manager import ConfigManager
from atlas.db import make_engine, make_sessionmaker
from atlas.db_init import ensure_schema


def _write_minimal_yaml_config(root_dir: Path) -> None:
    (root_dir / "config").mkdir(parents=True, exist_ok=True)
    (root_dir / "config" / "pipeline.yaml").write_text(
        "version: 1\nthresholds: { judge_cutoff_refine: 4, refine_max_retries: 1 }\nlimits: { chunk_max_chars: 1000 }\n",
        encoding="utf-8",
    )
    (root_dir / "config" / "models.yaml").write_text(
        "version: 1\n"
        "providers: { deterministic: { type: deterministic } }\n"
        "roles: {\n"
        "  embed_model: { provider: deterministic, model_name: deterministic-embed, params: { dim: 8 } },\n"
        "  judge_model: { provider: deterministic, model_name: deterministic-judge, params: {} },\n"
        "  refine_model: { provider: deterministic, model_name: deterministic-refine, params: {} },\n"
        "  metadata_tier1_model: { provider: deterministic, model_name: deterministic-meta1, params: {} },\n"
        "  metadata_tier2_model: { provider: deterministic, model_name: deterministic-meta2, params: {} }\n"
        "}\n",
        encoding="utf-8",
    )


class _FakeQdrantStore:
    last_points: list[Any] = []

    def __init__(self, *, url: str, api_key: str | None, collection: str):
        self._collection = collection

    @property
    def collection(self) -> str:
        return self._collection

    def ensure_collection(self, *, vector_size: int) -> None:
        assert vector_size > 0

    def upsert_points(self, *, points: list[Any]) -> None:
        _FakeQdrantStore.last_points = points


def _make_test_app(tmp_root: Path, monkeypatch: Any) -> FastAPI:
    _write_minimal_yaml_config(tmp_root)
    config_manager = ConfigManager(root_dir=tmp_root)

    db_path = tmp_root / "test.sqlite"
    engine = make_engine(f"sqlite+pysqlite:///{db_path}")
    ensure_schema(engine)
    session_factory = make_sessionmaker(engine)

    # Patch the store used by the pipeline runner.
    monkeypatch.setattr("atlas.pipeline.runner.QdrantStore", _FakeQdrantStore)

    app = FastAPI()
    app.include_router(make_admin_router(config_manager=config_manager, session_factory=session_factory))
    app.include_router(make_rag_router(config_manager=config_manager, session_factory=session_factory))
    return app


def test_pipeline_hitl_resume_commits_chunks(tmp_path: Path, monkeypatch: Any) -> None:
    app = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)
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
    assert _FakeQdrantStore.last_points

    # Verify fidelity_flag is present in the committed chunk payloads.
    for pt in _FakeQdrantStore.last_points:
        assert "fidelity_flag" in pt.payload, "fidelity_flag must be wired into chunk payloads"


def test_pipeline_hitl_resume_double_resume_rejected(tmp_path: Path, monkeypatch: Any) -> None:
    """A second resume call on an already-completed run must be rejected with 409."""
    app = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)
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
