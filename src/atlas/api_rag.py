from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter
from fastapi import HTTPException
from pydantic import BaseModel, Field
from qdrant_client.http import models as qm
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from atlas.config_manager import ConfigManager
from atlas.config_versions import get_active_config_version
from atlas.llm.registry import ModelRegistry
from atlas.rag.chunking import chunk_text
from atlas.rag.deterministic import deterministic_chunk_id, sha256_hex
from atlas.settings import Settings
from atlas.vectorstore.qdrant_store import QdrantHit, QdrantStore


class IngestTextRequest(BaseModel):
    doc_id: str
    doc_version: str = "1"
    text: str

    tenant_id: str | None = None
    project_id: str | None = None

    is_finalized: bool = True
    is_sensitive: bool = True

    source_mime_type: str = "text/plain"
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestTextResponse(BaseModel):
    ok: bool
    collection: str
    doc_id: str
    chunks_upserted: int


class SearchRequest(BaseModel):
    query: str
    top_k: int = 10

    tenant_id: str | None = None
    project_id: str | None = None


class SearchHit(BaseModel):
    id: str
    score: float
    text: str
    doc_id: str
    chunk_index: int
    payload: dict[str, Any]


class SearchResponse(BaseModel):
    ok: bool
    collection: str
    hits: list[SearchHit]


def _effective_config_payload(*, config_manager: ConfigManager, session_factory: sessionmaker[Session]) -> dict[str, Any]:
    yaml_defaults = config_manager.get()
    with session_factory() as session:
        active = get_active_config_version(session)

    if active is None:
        return {"pipeline": yaml_defaults.pipeline, "models": yaml_defaults.models}
    return active.payload


def make_rag_router(*, config_manager: ConfigManager, session_factory: sessionmaker[Session]) -> APIRouter:
    r = APIRouter(prefix="/rag", tags=["rag"])
    settings = Settings()

    @r.post("/ingest/text", response_model=IngestTextResponse)
    async def ingest_text(req: IngestTextRequest) -> IngestTextResponse:
        cfg = _effective_config_payload(config_manager=config_manager, session_factory=session_factory)
        pipeline_cfg = cfg.get("pipeline", {}) or {}
        models_cfg = cfg.get("models", {}) or {}

        limits = (pipeline_cfg.get("limits", {}) or {})
        max_chars = int(limits.get("chunk_max_chars", 1000))

        tenant_id = req.tenant_id or settings.atlas_default_tenant_id
        project_id = req.project_id or settings.atlas_default_project_id

        registry = ModelRegistry(settings=settings, models_cfg=models_cfg)
        resolved = registry.resolve("embed_model")
        provider = registry.provider_for(resolved.provider_name)

        chunks = chunk_text(text=req.text, max_chars=max_chars)
        texts = [c.text for c in chunks]
        if not texts:
            return IngestTextResponse(ok=True, collection="", doc_id=req.doc_id, chunks_upserted=0)

        try:
            vectors = await provider.embed(model=resolved.model_name, texts=texts, params=resolved.params)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(e)) from e
        if not vectors:
            return IngestTextResponse(ok=False, collection="", doc_id=req.doc_id, chunks_upserted=0)

        store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection="atlas_chunks")

        try:
            await run_in_threadpool(store.ensure_collection, vector_size=len(vectors[0]))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"Qdrant ensure_collection failed: {e}") from e

        now = dt.datetime.utcnow().isoformat() + "Z"
        points: list[qm.PointStruct] = []
        for c, v in zip(chunks, vectors, strict=True):
            content_hash = sha256_hex(c.text)
            pid = deterministic_chunk_id(
                doc_id=req.doc_id,
                doc_version=req.doc_version,
                content_hash=content_hash,
                chunk_index=c.index,
            )
            payload: dict[str, Any] = {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "doc_id": req.doc_id,
                "doc_version": req.doc_version,
                "chunk_index": c.index,
                "text": c.text,
                "content_hash": content_hash,
                "is_finalized": bool(req.is_finalized),
                "is_sensitive": bool(req.is_sensitive),
                "source_mime_type": req.source_mime_type,
                "embedding_provider": resolved.provider_name,
                "embedding_model": resolved.model_name,
                "embedding_params": resolved.params,
                "created_at": now,
                **(req.metadata or {}),
            }
            points.append(qm.PointStruct(id=pid, vector=v, payload=payload))

        try:
            await run_in_threadpool(store.upsert_points, points=points)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"Qdrant upsert failed: {e}") from e
        return IngestTextResponse(ok=True, collection=store.collection, doc_id=req.doc_id, chunks_upserted=len(points))

    @r.post("/search", response_model=SearchResponse)
    async def search(req: SearchRequest) -> SearchResponse:
        cfg = _effective_config_payload(config_manager=config_manager, session_factory=session_factory)
        models_cfg = cfg.get("models", {}) or {}

        tenant_id = req.tenant_id or settings.atlas_default_tenant_id
        project_id = req.project_id or settings.atlas_default_project_id

        registry = ModelRegistry(settings=settings, models_cfg=models_cfg)
        resolved = registry.resolve("embed_model")
        provider = registry.provider_for(resolved.provider_name)

        try:
            vectors = await provider.embed(model=resolved.model_name, texts=[req.query], params=resolved.params)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(e)) from e
        if not vectors:
            return SearchResponse(ok=False, collection="", hits=[])

        store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection="atlas_chunks")

        must = [
            qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=tenant_id)),
            qm.FieldCondition(key="project_id", match=qm.MatchValue(value=project_id)),
            qm.FieldCondition(key="is_finalized", match=qm.MatchValue(value=True)),
        ]
        hits: list[QdrantHit] = await run_in_threadpool(
            store.search,
            query_vector=vectors[0],
            limit=int(req.top_k),
            must=must,
        )

        out: list[SearchHit] = []
        for h in hits:
            out.append(
                SearchHit(
                    id=h.id,
                    score=h.score,
                    text=str(h.payload.get("text", "")),
                    doc_id=str(h.payload.get("doc_id", "")),
                    chunk_index=int(h.payload.get("chunk_index", -1)),
                    payload=h.payload,
                )
            )
        return SearchResponse(ok=True, collection=store.collection, hits=out)

    return r
