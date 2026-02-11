from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

import atlas.api_admin as api_admin
import atlas.api_rag as api_rag
import atlas.corpus_package as corpus_package
import atlas.export_package as export_package
from atlas.api_admin import make_admin_router
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


def _cond_value(cond: Any) -> tuple[str, Any] | None:
    try:
        key = str(getattr(cond, "key"))
        match = getattr(cond, "match", None)
        value = getattr(match, "value", None)
        return key, value
    except Exception:
        return None


class _MemQdrantStore:
    _points: dict[str, dict[str, Any]] = {}

    def __init__(self, *, url: str, api_key: str | None, collection: str):
        self._collection = collection

    @property
    def collection(self) -> str:
        return self._collection

    def ensure_collection(self, *, vector_size: int) -> None:
        assert int(vector_size) > 0

    def upsert_points(self, *, points: list[Any]) -> None:
        for p in points:
            pid = str(getattr(p, "id", ""))
            payload = dict(getattr(p, "payload", {}) or {})
            _MemQdrantStore._points[pid] = {"id": pid, "payload": payload}

    def _matches(self, payload: dict[str, Any], must: list[Any]) -> bool:
        for m in must or []:
            kv = _cond_value(m)
            if not kv:
                continue
            k, v = kv
            if payload.get(k) != v:
                return False
        return True

    def scroll_points(self, *, must: list[Any], limit: int = 256, max_points: int = 10_000) -> list[Any]:
        out: list[Any] = []
        for p in _MemQdrantStore._points.values():
            if self._matches(p.get("payload") or {}, must):
                out.append({"id": p["id"], "payload": dict(p.get("payload") or {})})
                if len(out) >= int(max_points):
                    break
        return out[: int(limit)] if int(limit) > 0 else out

    def set_payload(self, *, payload: dict[str, Any], must: list[Any]) -> None:
        for p in _MemQdrantStore._points.values():
            if self._matches(p.get("payload") or {}, must):
                p_payload = p.get("payload") or {}
                p_payload.update(payload or {})
                p["payload"] = p_payload

    def search(self, *, query_vector: list[float], limit: int, must: list[Any]) -> list[QdrantHit]:
        hits: list[QdrantHit] = []
        for p in _MemQdrantStore._points.values():
            payload = dict(p.get("payload") or {})
            if self._matches(payload, must):
                hits.append(QdrantHit(id=p["id"], score=1.0, payload=payload))
        return hits[: int(limit)]


def _make_test_app(tmp_root: Path, monkeypatch: Any) -> FastAPI:
    _write_minimal_yaml_config(tmp_root)
    config_manager = ConfigManager(root_dir=tmp_root)

    db_path = tmp_root / "test.sqlite"
    engine = make_engine(f"sqlite+pysqlite:///{db_path}")
    ensure_schema(engine)
    session_factory = make_sessionmaker(engine)

    artifacts_dir = tmp_root / "artifacts"
    monkeypatch.setenv("ATLAS_ARTIFACTS_DIR", str(artifacts_dir))

    _MemQdrantStore._points = {}
    monkeypatch.setattr(api_rag, "QdrantStore", _MemQdrantStore)
    monkeypatch.setattr(api_admin, "QdrantStore", _MemQdrantStore)
    monkeypatch.setattr("atlas.pipeline.runner.QdrantStore", _MemQdrantStore)
    monkeypatch.setattr(export_package, "QdrantStore", _MemQdrantStore)
    monkeypatch.setattr(corpus_package, "QdrantStore", _MemQdrantStore)

    app = FastAPI()
    app.include_router(make_rag_router(config_manager=config_manager, session_factory=session_factory))
    app.include_router(make_admin_router(config_manager=config_manager, session_factory=session_factory))
    return app


def test_corpus_export_then_import_roundtrip(tmp_path: Path, monkeypatch: Any) -> None:
    app = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)
    client = TestClient(app)

    # Ingest a doc into corpus A.
    ingest = client.post(
        "/rag/ingest/text",
        json={
            "doc_id": "doc1",
            "doc_version": "v1",
            "text": "# Title\n\nHello world",
            "corpus_id": "corpA",
        },
    )
    assert ingest.status_code == 200
    assert ingest.json()["ok"] is True

    # Export corpus A.
    exp = client.get("/admin/corpora/corpA/export")
    assert exp.status_code == 200
    assert exp.headers["content-type"].startswith("application/zip")

    outer = zipfile.ZipFile(io.BytesIO(exp.content))
    names = set(outer.namelist())
    assert "corpus_manifest.json" in names
    doc_zips = [n for n in names if n.startswith("docs/") and n.endswith(".zip")]
    assert doc_zips

    # Validate one inner doc package.
    inner_bytes = outer.read(doc_zips[0])
    inner = zipfile.ZipFile(io.BytesIO(inner_bytes))
    inner_names = set(inner.namelist())
    assert "manifest.json" in inner_names
    assert "document.md" in inner_names

    # Import into corpus B.
    files = {"file": ("corp.zip", exp.content, "application/zip")}
    imp = client.post("/admin/corpora/corpB/import", files=files)
    assert imp.status_code == 200
    data = imp.json()
    assert data["ok"] is True
    assert data["docs_imported"] >= 1

    # Ensure points exist for the imported corpus and are searchable.
    has_corp_b = False
    for p in _MemQdrantStore._points.values():
        payload = p.get("payload") or {}
        if payload.get("corpus_id") == "corpB" and payload.get("is_finalized") is True and payload.get("is_active_version") is True:
            has_corp_b = True
            break
    assert has_corp_b

    # Verify search is corpus-scoped.
    s_a = client.post("/rag/search", json={"query": "hello", "top_k": 5, "corpus_id": "corpA"}).json()
    s_b = client.post("/rag/search", json={"query": "hello", "top_k": 5, "corpus_id": "corpB"}).json()
    assert s_a["hits"]
    assert s_b["hits"]
