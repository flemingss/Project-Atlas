from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from atlas.retry import RetryConfig, get_retry_config, sync_retry

log = logging.getLogger(__name__)


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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _retry_cfg(self) -> RetryConfig:
        cfg = get_retry_config("vectorstore")
        # Only retry on connection / timeout exceptions from qdrant-client.
        # Import qdrant exceptions lazily so we don't crash if the API changes.
        retryable: list[type] = [ConnectionError, TimeoutError, OSError]
        try:
            from qdrant_client.http.exceptions import UnexpectedResponse
            retryable.append(UnexpectedResponse)
        except ImportError:
            pass
        try:
            from httpx import RequestError
            retryable.append(RequestError)
        except ImportError:
            pass
        return RetryConfig(
            max_retries=cfg.max_retries,
            base_delay_s=cfg.base_delay_s,
            max_delay_s=cfg.max_delay_s,
            jitter=cfg.jitter,
            retryable_exceptions=tuple(retryable),
        )

    # ------------------------------------------------------------------
    # Public API — with retry
    # ------------------------------------------------------------------

    def ensure_collection(self, *, vector_size: int) -> None:
        if self._client.collection_exists(self._collection):
            # Validate vector dimension matches existing collection.
            try:
                info = self._client.get_collection(self._collection)
                existing_size = int(info.config.params.vectors.size)
            except Exception:  # noqa: BLE001
                # If we cannot introspect (older client/server), skip validation.
                return

            if existing_size != int(vector_size):
                raise ValueError(
                    "Qdrant collection vector dimension mismatch: "
                    f"collection='{self._collection}' expects dim={existing_size}, got dim={int(vector_size)}. "
                    "Use a different collection name or ensure your embedding model dimension matches the existing collection."
                )
            return
        self._client.create_collection(
            collection_name=self._collection,
            vectors_config=qm.VectorParams(size=vector_size, distance=qm.Distance.COSINE),
        )

    # A multi-thousand-page document chunks into thousands of points; sending
    # them in one request risks Qdrant's REST payload limit (~32MB) and makes
    # a transient failure retry the entire set. 512 points ≈ 2-3MB per call.
    _UPSERT_BATCH = 512

    def upsert_points(self, *, points: list[qm.PointStruct]) -> None:
        if not points:
            return

        for start in range(0, len(points), self._UPSERT_BATCH):
            batch = points[start : start + self._UPSERT_BATCH]

            def _do(b: list[qm.PointStruct] = batch) -> None:
                self._client.upsert(collection_name=self._collection, points=b, wait=True)

            sync_retry(_do, config=self._retry_cfg(), subsystem="vectorstore", operation="upsert")

    def search(
        self,
        *,
        query_vector: list[float],
        limit: int,
        must: list[qm.FieldCondition],
    ) -> list[QdrantHit]:
        def _do() -> list[QdrantHit]:
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

        return sync_retry(_do, config=self._retry_cfg(), subsystem="vectorstore", operation="search")

    def set_payload(self, *, payload: dict[str, Any], must: list[qm.FieldCondition]) -> None:
        """Update payload for points matching a filter."""

        def _do() -> None:
            self._client.set_payload(
                collection_name=self._collection,
                payload=payload,
                points=qm.Filter(must=must),
                wait=True,
            )

        sync_retry(_do, config=self._retry_cfg(), subsystem="vectorstore", operation="set_payload")

    def delete_by_filter(self, *, must: list[qm.FieldCondition]) -> None:
        """Delete all points matching a filter."""

        def _do() -> None:
            self._client.delete(
                collection_name=self._collection,
                points_selector=qm.FilterSelector(
                    filter=qm.Filter(must=must),
                ),
                wait=True,
            )

        sync_retry(_do, config=self._retry_cfg(), subsystem="vectorstore", operation="delete")

    def scroll_points(
        self,
        *,
        must: list[qm.FieldCondition],
        limit: int = 256,
        max_points: int = 10_000,
    ) -> list[Any]:
        """Return all points matching filter (best-effort) for export/debug."""

        def _do() -> list[Any]:
            out: list[Any] = []
            offset: str | int | None = None

            while True:
                points, next_offset = self._client.scroll(
                    collection_name=self._collection,
                    scroll_filter=qm.Filter(must=must),
                    limit=int(limit),
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                out.extend(points)
                if next_offset is None:
                    break
                offset = next_offset
                if len(out) >= int(max_points):
                    break
            return out

        return sync_retry(_do, config=self._retry_cfg(), subsystem="vectorstore", operation="scroll")
