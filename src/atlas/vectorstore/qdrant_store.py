from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm


@dataclass(frozen=True)
class QdrantHit:
    id: str
    score: float
    payload: dict[str, Any]


class QdrantStore:
    def __init__(
        self,
        *,
        url: str,
        api_key: str | None = None,
        collection: str,
    ):
        self._collection = collection
        self._client = QdrantClient(url=url, api_key=api_key, check_compatibility=False)

    @property
    def collection(self) -> str:
        return self._collection

    def ensure_collection(self, *, vector_size: int) -> None:
        if self._client.collection_exists(self._collection):
            return
        self._client.create_collection(
            collection_name=self._collection,
            vectors_config=qm.VectorParams(size=vector_size, distance=qm.Distance.COSINE),
        )

    def upsert_points(self, *, points: list[qm.PointStruct]) -> None:
        if not points:
            return
        self._client.upsert(collection_name=self._collection, points=points, wait=True)

    def search(
        self,
        *,
        query_vector: list[float],
        limit: int,
        must: list[qm.FieldCondition],
    ) -> list[QdrantHit]:
        # qdrant-client >= 1.16 uses `query_points` rather than `search`.
        res = self._client.query_points(
            collection_name=self._collection,
            query=query_vector,
            limit=limit,
            with_payload=True,
            query_filter=qm.Filter(must=must),
        )
        hits: list[QdrantHit] = []
        for r in res.points:
            hits.append(QdrantHit(id=str(r.id), score=float(r.score), payload=r.payload or {}))
        return hits
