from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

import atlas.api_rag as api_rag
from atlas.api_rag import make_rag_router
from atlas.config_manager import ConfigManager
from atlas.db import make_engine, make_sessionmaker
from atlas.db_init import ensure_schema
from atlas.vectorstore.qdrant_store import QdrantHit


def _write_minimal_yaml_config(root_dir: Path) -> None:
    (root_dir / "config").mkdir(parents=True, exist_ok=True)
    (root_dir / "config" / "pipeline.yaml").write_text(
        "version: 1\nlimits: { chunk_max_chars: 1000 }\n",
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
    last_search_must: list[Any] = []
    last_set_payload_calls: list[dict[str, Any]] = []

    def __init__(self, *, url: str, api_key: str | None, collection: str):
        self._collection = collection

    @property
    def collection(self) -> str:
        return self._collection

    def ensure_collection(self, *, vector_size: int) -> None:
        assert vector_size > 0

    def upsert_points(self, *, points: list[Any]) -> None:
        _FakeQdrantStore.last_points = points

    def set_payload(self, *, payload: dict[str, Any], must: list[Any]) -> None:
        _FakeQdrantStore.last_set_payload_calls.append({"payload": payload, "must": must})

    def scroll_points(self, *, must: list[Any], limit: int = 256, max_points: int = 10_000) -> list[Any]:
        return []

    def search(self, *, query_vector: list[float], limit: int, must: list[Any]) -> list[QdrantHit]:
        _FakeQdrantStore.last_search_must = must
        return [
            QdrantHit(
                id=str(uuid.uuid4()),
                score=0.9,
                payload={"doc_id": "d1", "chunk_index": 0, "text": "hello"},
            )
        ]


def _make_test_app(tmp_root: Path, monkeypatch: Any) -> FastAPI:
    _write_minimal_yaml_config(tmp_root)
    config_manager = ConfigManager(root_dir=tmp_root)

    # In-memory DB for config version lookup (rag router reads effective config).
    db_path = tmp_root / "test.sqlite"
    engine = make_engine(f"sqlite+pysqlite:///{db_path}")
    ensure_schema(engine)
    session_factory = make_sessionmaker(engine)

    # Mock Qdrant store to avoid docker dependency.
    _FakeQdrantStore.last_points = []
    _FakeQdrantStore.last_search_must = []
    _FakeQdrantStore.last_set_payload_calls = []
    monkeypatch.setattr(api_rag, "QdrantStore", _FakeQdrantStore)
    monkeypatch.setattr("atlas.pipeline.runner.QdrantStore", _FakeQdrantStore)

    app = FastAPI()
    app.include_router(make_rag_router(config_manager=config_manager, session_factory=session_factory))
    return app


def test_rag_ingest_text_upserts_uuid_ids(tmp_path: Path, monkeypatch: Any) -> None:
    app = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)
    client = TestClient(app)

    res = client.post(
        "/rag/ingest/text",
        json={"doc_id": "demo", "doc_version": "v1", "text": "hello world"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["chunks_upserted"] == 1

    assert _FakeQdrantStore.last_points
    pid = _FakeQdrantStore.last_points[0].id
    uuid.UUID(str(pid))


def test_rag_search_returns_hits_and_applies_filters(tmp_path: Path, monkeypatch: Any) -> None:
    app = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)
    client = TestClient(app)

    res = client.post("/rag/search", json={"query": "hello", "top_k": 3})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["hits"]

    # Sanity check: we pass tenant/project/finalized filter to the store.
    assert len(_FakeQdrantStore.last_search_must) == 4
