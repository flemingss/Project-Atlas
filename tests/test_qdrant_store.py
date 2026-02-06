from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import atlas.vectorstore.qdrant_store as qs


@dataclass
class _FakePoint:
    id: str
    score: float
    payload: dict[str, Any] | None = None


@dataclass
class _FakeQueryResponse:
    points: list[_FakePoint]


class _FakeQdrantClient:
    def __init__(self, *, url: str, api_key: str | None = None, check_compatibility: bool = True):
        self.url = url
        self.created: list[tuple[str, int]] = []
        self.upserts: list[tuple[str, int]] = []
        self.queries: list[dict[str, Any]] = []
        self._exists = False

    def collection_exists(self, name: str) -> bool:
        return self._exists

    def create_collection(self, *, collection_name: str, vectors_config: Any) -> None:
        self._exists = True
        self.created.append((collection_name, int(vectors_config.size)))

    def upsert(self, *, collection_name: str, points: list[Any], wait: bool) -> None:
        self.upserts.append((collection_name, len(points)))

    def query_points(self, *, collection_name: str, query: list[float], limit: int, with_payload: bool, query_filter: Any):
        self.queries.append({"collection": collection_name, "dim": len(query), "limit": limit})
        return _FakeQueryResponse(points=[_FakePoint(id="p1", score=0.5, payload={"k": "v"})])


def test_qdrant_store_uses_query_points(monkeypatch: Any) -> None:
    monkeypatch.setattr(qs, "QdrantClient", _FakeQdrantClient)
    store = qs.QdrantStore(url="http://localhost:6333", api_key=None, collection="c")

    store.ensure_collection(vector_size=3)
    hits = store.search(query_vector=[0.0, 0.0, 0.0], limit=2, must=[])
    assert hits
    assert hits[0].id == "p1"
    assert hits[0].payload["k"] == "v"
