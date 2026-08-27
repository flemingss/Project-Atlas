from __future__ import annotations

import uuid

import httpx
import pytest
from qdrant_client.http import models as qm

from atlas.settings import Settings
from atlas.vectorstore.qdrant_store import QdrantStore


def _qdrant_reachable(url: str) -> bool:
    try:
        r = httpx.get(f"{url.rstrip('/')}/collections", timeout=1.5)
        return r.status_code == 200
    except Exception:
        return False


@pytest.mark.integration
def test_qdrant_store_roundtrip_live() -> None:
    settings = Settings()
    if not _qdrant_reachable(settings.atlas_qdrant_url):
        pytest.skip(f"Qdrant not reachable at {settings.atlas_qdrant_url}")

    collection = f"test_atlas_{uuid.uuid4().hex}"
    store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection=collection)

    store.ensure_collection(vector_size=3)

    points = [
        qm.PointStruct(id=str(uuid.uuid4()), vector=[0.1, 0.0, 0.0], payload={"tenant_id": "t", "project_id": "p", "is_finalized": True}),
        qm.PointStruct(id=str(uuid.uuid4()), vector=[0.0, 0.1, 0.0], payload={"tenant_id": "t", "project_id": "p", "is_finalized": True}),
    ]
    store.upsert_points(points=points)

    hits = store.search(
        query_vector=[0.1, 0.0, 0.0],
        limit=2,
        must=[
            qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value="t")),
            qm.FieldCondition(key="project_id", match=qm.MatchValue(value="p")),
            qm.FieldCondition(key="is_finalized", match=qm.MatchValue(value=True)),
        ],
    )
    assert hits

    # Best-effort cleanup (don’t fail the test if cleanup errors).
    try:
        store._client.delete_collection(collection_name=collection)
    except Exception:
        pass
