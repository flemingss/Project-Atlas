from __future__ import annotations
import mimetypes

from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi import HTTPException
from pydantic import BaseModel, Field
from qdrant_client.http import models as qm
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from atlas.auth import require_admin_token
from atlas.config_manager import ConfigManager
from atlas.config_versions import get_active_config_version
from atlas.llm.registry import ModelRegistry
from atlas.pipeline.runner import ingest_file_via_pipeline, ingest_text_via_pipeline
from atlas.settings import Settings
from atlas.vectorstore.qdrant_store import QdrantHit, QdrantStore


def _normalize_error_code(code: Any) -> str | None:
    if not code:
        return None
    # ErrorCode is a str Enum; `.value` is the stable wire format.
    value = getattr(code, "value", None)
    if isinstance(value, str) and value:
        return value
    if isinstance(code, str):
        # Some callsites may have serialized Enums using `str(ErrorCode.X)`.
        if "." in code:
            return code.rsplit(".", 1)[-1]
        return code
    return str(code)


class IngestTextRequest(BaseModel):
    doc_id: str
    doc_version: str = "1"
    text: str

    tenant_id: str | None = None
    project_id: str | None = None
    corpus_id: str | None = None

    is_finalized: bool = True
    is_sensitive: bool = True

    source_mime_type: str = "text/plain"
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestTextResponse(BaseModel):
    ok: bool
    collection: str
    doc_id: str
    chunks_upserted: int
    error_code: str | None = None
    error_message: str | None = None


class SearchRequest(BaseModel):
    query: str
    top_k: int = 10

    tenant_id: str | None = None
    project_id: str | None = None
    corpus_id: str | None = None

    # Fidelity-aware retrieval:
    #   "verified"    → only chunks with fidelity_flag == "verified"  (default, highest precision)
    #   "verified+partial" → fidelity_flag in {"verified", "partial"}
    #   "all"         → no fidelity filter (high-recall / debug mode)
    fidelity_mode: str = "verified"


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
    # Same auth posture as /admin: open in dev (bypass or no token
    # configured), token-required otherwise. Ingest is a write path and
    # search returns document content — neither should be anonymous on a
    # non-dev deployment.
    r = APIRouter(prefix="/rag", tags=["rag"], dependencies=[Depends(require_admin_token)])
    settings = Settings()

    @r.post("/ingest/text", response_model=IngestTextResponse)
    async def ingest_text(req: IngestTextRequest) -> IngestTextResponse:
        tenant_id = req.tenant_id or settings.atlas_default_tenant_id
        project_id = req.project_id or settings.atlas_default_project_id
        corpus_id = req.corpus_id or settings.atlas_default_corpus_id

        try:
            result = await ingest_text_via_pipeline(
                config_manager=config_manager,
                session_factory=session_factory,
                doc_id=req.doc_id,
                doc_version=req.doc_version,
                tenant_id=tenant_id,
                project_id=project_id,
                corpus_id=corpus_id,
                text=req.text,
                source_mime_type=req.source_mime_type,
                is_finalized=bool(req.is_finalized),
                is_sensitive=bool(req.is_sensitive),
                metadata=req.metadata or {},
            )
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(e)) from e

        return IngestTextResponse(
            ok=bool(result.get("ok")),
            collection=str(result.get("collection", "")),
            doc_id=req.doc_id,
            chunks_upserted=int(result.get("chunks_upserted", 0)),
            error_code=_normalize_error_code(result.get("error_code")),
            error_message=(str(result.get("error_message")) if result.get("error_message") else None),
        )

    @r.post("/ingest/file", response_model=IngestTextResponse)
    async def ingest_file(
        file: UploadFile = File(...),
        doc_id: str = Form(...),
        doc_version: str = Form("1"),
        tenant_id: str | None = Form(None),
        project_id: str | None = Form(None),
        corpus_id: str | None = Form(None),
        is_finalized: bool = Form(True),
        is_sensitive: bool = Form(True),
        source_mime_type: str | None = Form(None),
    ) -> IngestTextResponse:
        t_id = tenant_id or settings.atlas_default_tenant_id
        p_id = project_id or settings.atlas_default_project_id
        c_id = corpus_id or settings.atlas_default_corpus_id

        try:
            body = await file.read()
            if len(body) > int(settings.atlas_pdf_max_bytes):
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"Upload exceeds size limit "
                        f"({len(body)} > {int(settings.atlas_pdf_max_bytes)} bytes)"
                    ),
                )
            mime = source_mime_type or file.content_type or "application/octet-stream"
            if mime == "application/octet-stream" and file.filename:
                guessed, _ = mimetypes.guess_type(file.filename)
                if guessed:
                    mime = guessed
            result = await ingest_file_via_pipeline(
                config_manager=config_manager,
                session_factory=session_factory,
                doc_id=doc_id,
                doc_version=doc_version,
                tenant_id=t_id,
                project_id=p_id,
                corpus_id=c_id,
                file_bytes=body,
                filename=file.filename,
                source_mime_type=mime,
                is_finalized=bool(is_finalized),
                is_sensitive=bool(is_sensitive),
                metadata={},
            )
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(e)) from e

        return IngestTextResponse(
            ok=bool(result.get("ok")),
            collection=str(result.get("collection", "")),
            doc_id=doc_id,
            chunks_upserted=int(result.get("chunks_upserted", 0)),
            error_code=_normalize_error_code(result.get("error_code")),
            error_message=(str(result.get("error_message")) if result.get("error_message") else None),
        )

    @r.post("/search", response_model=SearchResponse)
    async def search(req: SearchRequest) -> SearchResponse:
        cfg = _effective_config_payload(config_manager=config_manager, session_factory=session_factory)
        models_cfg = cfg.get("models", {}) or {}

        tenant_id = req.tenant_id or settings.atlas_default_tenant_id
        project_id = req.project_id or settings.atlas_default_project_id
        corpus_id = req.corpus_id or settings.atlas_default_corpus_id

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
            qm.FieldCondition(key="corpus_id", match=qm.MatchValue(value=corpus_id)),
            qm.FieldCondition(key="is_finalized", match=qm.MatchValue(value=True)),
            qm.FieldCondition(key="is_active_version", match=qm.MatchValue(value=True)),
        ]

        # Fidelity-aware filtering: restrict to high-confidence chunks unless
        # the caller explicitly opts into lower-fidelity or "all" mode.
        fmode = (req.fidelity_mode or "verified").strip().lower()
        if fmode == "verified":
            must.append(qm.FieldCondition(key="fidelity_flag", match=qm.MatchValue(value="verified")))
        elif fmode in ("verified+partial", "verified_partial"):
            must.append(
                qm.FieldCondition(
                    key="fidelity_flag",
                    match=qm.MatchAny(any=["verified", "partial"]),
                )
            )
        # fmode == "all" → no fidelity filter appended
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
