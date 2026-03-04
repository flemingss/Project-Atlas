"""Shared test helpers for Project Atlas unit tests.

Provides:
- write_minimal_yaml_config: Write config files into a temp directory.
- FakeQdrantStore: In-memory Qdrant mock supporting all store operations.
- make_test_app: Create a SQLite-backed FastAPI app with FakeQdrantStore patched in.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

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


def write_minimal_yaml_config(
    root_dir: Path,
    *,
    provider: str = "deterministic",
    include_all_roles: bool = True,
) -> None:
    """Write pipeline.yaml and models.yaml into ``root_dir/config``.

    When *provider* is ``"deterministic"`` and *include_all_roles* is ``True``
    (the default) a full 5-role deterministic config is written.  Otherwise a
    minimal embed-only lmstudio config is written.
    """
    (root_dir / "config").mkdir(parents=True, exist_ok=True)
    (root_dir / "config" / "pipeline.yaml").write_text(
        "version: 1\n"
        "thresholds: { judge_cutoff_refine: 4, refine_max_retries: 2 }\n"
        "limits: { chunk_max_chars: 1000 }\n",
        encoding="utf-8",
    )
    if provider == "lmstudio" or not include_all_roles:
        models_yaml = (
            "version: 1\n"
            "providers: { lmstudio: { type: openai_compat } }\n"
            "roles: { embed_model: { provider: lmstudio, model_name: text-embedding, params: {} } }\n"
        )
    else:
        models_yaml = (
            "version: 1\n"
            "providers: { deterministic: { type: deterministic } }\n"
            "roles: {\n"
            "  embed_model: { provider: deterministic, model_name: deterministic-embed, params: { dim: 8 } },\n"
            "  judge_model: { provider: deterministic, model_name: deterministic-judge, params: {} },\n"
            "  refine_model: { provider: deterministic, model_name: deterministic-refine, params: {} },\n"
            "  metadata_tier1_model: { provider: deterministic, model_name: deterministic-meta1, params: {} },\n"
            "  metadata_tier2_model: { provider: deterministic, model_name: deterministic-meta2, params: {} }\n"
            "}\n"
        )
    (root_dir / "config" / "models.yaml").write_text(models_yaml, encoding="utf-8")


class FakeQdrantStore:
    """Unified in-memory Qdrant mock for unit tests.

    Stores points in a class-level dict so all instances in a test share state.
    Call ``FakeQdrantStore.reset()`` (done automatically by :func:`make_test_app`)
    between tests to clear accumulated state.
    """

    # Internal dict storage: pid -> {"id": pid, "payload": {...}}
    _storage: dict[str, Any] = {}
    # Accumulated list of original point objects (supports .payload attribute access)
    last_points: list[Any] = []
    upsert_count: int = 0
    last_search_must: list[Any] = []
    last_set_payload_calls: list[dict[str, Any]] = []

    def __init__(self, *, url: str, api_key: str | None, collection: str):
        self._collection = collection

    @property
    def collection(self) -> str:
        return self._collection

    @classmethod
    def reset(cls) -> None:
        cls._storage = {}
        cls.last_points = []
        cls.upsert_count = 0
        cls.last_search_must = []
        cls.last_set_payload_calls = []

    def ensure_collection(self, *, vector_size: int) -> None:
        assert int(vector_size) > 0

    def upsert_points(self, *, points: list[Any]) -> None:
        # Update internal dict storage (upsert semantics: replace by ID)
        for p in points:
            pid = str(getattr(p, "id", ""))
            payload = dict(getattr(p, "payload", {}) or {})
            FakeQdrantStore._storage[pid] = {"id": pid, "payload": payload}
        # Accumulate original point objects, replacing by id
        existing_by_id = {getattr(pt, "id", ""): pt for pt in FakeQdrantStore.last_points}
        for p in points:
            existing_by_id[getattr(p, "id", "")] = p
        FakeQdrantStore.last_points = list(existing_by_id.values())
        FakeQdrantStore.upsert_count += len(points)

    def _matches(self, payload: dict[str, Any], must: list[Any]) -> bool:
        for m in must or []:
            try:
                key = str(getattr(m, "key"))
                match_obj = getattr(m, "match", None)
                value = getattr(match_obj, "value", None)
                # Support MatchAny (e.g. fidelity_mode = "verified+partial").
                any_values = getattr(match_obj, "any", None)
            except Exception:  # noqa: BLE001
                continue
            if any_values is not None:
                if payload.get(key) not in any_values:
                    return False
            elif payload.get(key) != value:
                return False
        return True

    def scroll_points(
        self, *, must: list[Any], limit: int = 256, max_points: int = 10_000
    ) -> list[Any]:
        out: list[Any] = []
        for p in FakeQdrantStore._storage.values():
            if self._matches(p.get("payload") or {}, must):
                out.append({"id": p["id"], "payload": dict(p.get("payload") or {})})
                if len(out) >= int(max_points):
                    break
        return out[: int(limit)] if int(limit) > 0 else out

    def set_payload(self, *, payload: dict[str, Any], must: list[Any]) -> None:
        FakeQdrantStore.last_set_payload_calls.append({"payload": payload, "must": must})
        for p in FakeQdrantStore._storage.values():
            if self._matches(p.get("payload") or {}, must):
                p_payload = p.get("payload") or {}
                p_payload.update(payload or {})
                p["payload"] = p_payload

    def delete_by_filter(self, *, must: list[Any]) -> None:
        to_delete: list[str] = []
        for pid, point in FakeQdrantStore._storage.items():
            if self._matches(point.get("payload") or {}, must):
                to_delete.append(pid)
        for pid in to_delete:
            FakeQdrantStore._storage.pop(pid, None)
        FakeQdrantStore.last_points = [
            p for p in FakeQdrantStore.last_points
            if str(getattr(p, "id", "")) not in set(to_delete)
        ]

    def search(
        self, *, query_vector: list[float], limit: int, must: list[Any]
    ) -> list[QdrantHit]:
        FakeQdrantStore.last_search_must = must
        hits: list[QdrantHit] = []
        for p in FakeQdrantStore._storage.values():
            payload = dict(p.get("payload") or {})
            if self._matches(payload, must):
                hits.append(QdrantHit(id=p["id"], score=1.0, payload=payload))
        if not hits:
            # Fallback: return a dummy hit so tests that call search without prior
            # ingestion still receive a non-empty result list.
            hits = [
                QdrantHit(
                    id=str(uuid.uuid4()),
                    score=0.9,
                    payload={"doc_id": "d1", "chunk_index": 0, "text": "hello"},
                )
            ]
        return hits[: int(limit)]


def make_test_app(
    tmp_root: Path,
    monkeypatch: Any,
    *,
    include_rag: bool = True,
    include_admin: bool = True,
) -> tuple[FastAPI, sessionmaker]:  # type: ignore[type-arg]
    """Create a SQLite-backed FastAPI test app with :class:`FakeQdrantStore` patched in.

    Returns ``(app, session_factory)`` so tests that need direct DB access can use
    the session factory.
    """
    write_minimal_yaml_config(tmp_root)
    config_manager = ConfigManager(root_dir=tmp_root)

    db_path = tmp_root / "test.sqlite"
    engine = make_engine(f"sqlite+pysqlite:///{db_path}")
    ensure_schema(engine)
    session_factory = make_sessionmaker(engine)

    artifacts_dir = tmp_root / "artifacts"
    monkeypatch.setenv("ATLAS_ARTIFACTS_DIR", str(artifacts_dir))

    FakeQdrantStore.reset()
    monkeypatch.setattr("atlas.pipeline.runner.QdrantStore", FakeQdrantStore)
    monkeypatch.setattr(api_rag, "QdrantStore", FakeQdrantStore)
    monkeypatch.setattr(api_admin, "QdrantStore", FakeQdrantStore)
    monkeypatch.setattr(export_package, "QdrantStore", FakeQdrantStore)
    monkeypatch.setattr(corpus_package, "QdrantStore", FakeQdrantStore)

    app = FastAPI()
    if include_rag:
        app.include_router(make_rag_router(config_manager=config_manager, session_factory=session_factory))
    if include_admin:
        app.include_router(make_admin_router(config_manager=config_manager, session_factory=session_factory))
    return app, session_factory
