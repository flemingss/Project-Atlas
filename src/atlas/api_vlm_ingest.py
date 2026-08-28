"""VLM Ingest API — interactive + headless document ingestion via VLM.

Provides endpoints for the VLM-first ingest workflow:

1. Start a session (upload PDF or reference existing run)
2. Preview page thumbnails
3. Configure per-page settings (DPI, crop)
4. Process pages one-at-a-time through VLM
5. Stitch results deterministically
6. Commit to pipeline / save as artifact

All endpoints are mounted under ``/api/editor/vlm-ingest``.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import sessionmaker
from starlette.concurrency import run_in_threadpool

from atlas.auth import require_admin_token
from atlas.config_manager import ConfigManager
from atlas.ingest.page_renderer import (
    CropMargins,
    analyze_page,
    build_vision_messages,
    page_count,
    render_page,
    render_page_base64,
)
from atlas.llm.provider import ChatMessage
from atlas.llm.registry import ModelRegistry
from atlas.pipeline.runner import ingest_text_via_pipeline
from atlas.settings import Settings
from atlas.vlm_ingest import store as vlm_store
from atlas.vlm_ingest.session import (
    PageStatus,
    SessionRegistry,
    SessionStatus,
    VlmIngestConfig,
    VlmIngestSession,
)
from atlas.vlm_ingest.stitcher import PageResult
from atlas.workflow_ledger import (
    ArtifactRefCreateRequest,
    WorkflowRunCreateRequest,
    add_artifact_ref,
    create_workflow_run,
    get_workflow_run,
    list_artifact_refs,
)

log = logging.getLogger(__name__)
diag_log = logging.getLogger("uvicorn.error")


def _diag(message: str, *args: Any) -> None:
    try:
        text = message % args if args else message
    except Exception:
        text = message
    diag_log.info(text)
    print(f"[VLM_DIAG] {text}", flush=True)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class StartSessionRequest(BaseModel):
    """Start a VLM ingest session from an existing run."""
    run_id: int
    config: dict[str, Any] | None = None
    headless: bool = False


class StartSessionResponse(BaseModel):
    session_id: str
    page_count: int
    source_filename: str
    status: str
    headless: bool


class PageSettingsUpdate(BaseModel):
    """Per-page setting overrides."""
    page_num: int
    enabled: bool | None = None
    dpi: int | None = None
    crop_top: float | None = None
    crop_bottom: float | None = None
    crop_left: float | None = None
    crop_right: float | None = None
    mask_regions: list[dict] | None = None


class UpdateConfigRequest(BaseModel):
    """Update global config and/or per-page overrides."""
    dpi: int | None = None
    crop_top: float | None = None
    crop_bottom: float | None = None
    crop_left: float | None = None
    crop_right: float | None = None
    system_prompt: str | None = None
    page_overrides: list[PageSettingsUpdate] | None = None


class ProcessPageRequest(BaseModel):
    """Process a specific page through VLM."""
    page_num: int | None = None  # None = auto-pick next pending


class ProcessPageResponse(BaseModel):
    page_num: int
    markdown: str
    model: str
    status: str
    # Reserved for providers that surface a stop reason (e.g. "stop", "length").
    # Not populated by the current implementation but kept for forward compatibility.
    finish_reason: str | None = None
    error: str | None = None  # set when status="error"


class StitchResponse(BaseModel):
    markdown: str
    page_count: int
    pages_processed: int
    duplicate_lines_removed: int
    tables_merged: int
    headings_merged: int


class ProcessAllResponse(BaseModel):
    """Result of bulk (server-side) page processing."""
    pages_processed: int
    pages_skipped: int
    pages_failed: int
    errors: dict[int, str] = {}  # page_num → error message
    stitch: StitchResponse | None = None  # auto-stitched result


class CommitRequest(BaseModel):
    """Commit stitched markdown — save as artifact and feed into pipeline."""
    markdown: str | None = None  # None = use stitched result
    feed_pipeline: bool = True   # chunk, embed, upsert to Qdrant after saving
    tenant_id: str | None = None
    project_id: str | None = None
    corpus_id: str | None = None


class CommitResponse(BaseModel):
    run_id: int | None
    path: str
    chars: int
    chunks_upserted: int = 0
    message: str
    warnings: list[str] | None = None  # non-empty when pipeline upsert fails


class PageSummary(BaseModel):
    page_num: int
    status: str
    enabled: bool
    markdown: str
    model: str


class SessionSummary(BaseModel):
    session_id: str
    status: str
    source_filename: str
    run_id: int | None
    page_count: int
    headless: bool
    progress: dict[str, int]
    config: dict[str, Any]
    pages: list[PageSummary] = []


class ResumableSession(BaseModel):
    """A session the operator can pick back up.

    Deliberately lighter than :class:`SessionSummary` — this is a chooser list,
    so it carries no page bodies. Sourced from the ledger, so it includes
    sessions the in-memory cache has already released.
    """

    session_id: str
    source_filename: str
    page_count: int
    status: str
    run_id: int | None = None
    pages_done: int = 0
    updated_at: str = ""


class ExportConfigResponse(BaseModel):
    """Exported session config for headless reuse."""
    config: dict[str, Any]
    source_filename: str
    page_count: int


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def make_vlm_ingest_router(
    *,
    config_manager: ConfigManager,
    session_factory: sessionmaker,
) -> APIRouter:
    """Create the VLM ingest API router."""
    settings = Settings()
    artifacts_dir = Path(settings.atlas_artifacts_dir).resolve()
    registry = SessionRegistry(max_sessions=50, ttl_seconds=3600.0)

    r = APIRouter(
        prefix="/api/editor/vlm-ingest",
        tags=["vlm-ingest"],
        dependencies=[Depends(require_admin_token)],
    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_session(sid: str) -> VlmIngestSession:
        s = registry.get(sid)
        if s is None:
            # Sessions no longer expire — the registry miss already tried a
            # rehydrate from the ledger. Reaching here means the record is
            # genuinely absent: discarded, or removed by a data reset.
            raise HTTPException(
                status_code=404,
                detail=f"Session '{sid}' does not exist (discarded or reset)",
            )
        return s

    def _arm_session(s: VlmIngestSession, pdf_bytes: bytes) -> str:
        """Make a new session durable, and return the source PDF's sha256.

        Three things land before the operator can do any work:

        * the source PDF on disk, so an evicted session can be rebuilt in full
          — previews, thumbnails and re-processing all need the bytes back;
        * the session row in the ledger, which is the system of record;
        * a write-through writer, so each page result is persisted the moment
          it settles rather than at the end of the job.

        The human-readable ``page_XXXX.md`` checkpoints are kept as well: they
        cost nothing and give the operator something to salvage by hand.
        """
        d = artifacts_dir / "vlm_sessions" / s.session_id
        s.checkpoint_dir = str(d)
        s.writer = vlm_store.LedgerSessionWriter(session_factory)

        sha = hashlib.sha256(pdf_bytes).hexdigest()
        pdf_path: Path | None = d / "source.pdf"
        try:
            d.mkdir(parents=True, exist_ok=True)
            # Write-then-rename: a crash partway through a direct write would
            # leave a truncated source.pdf that rehydrate cannot render, and
            # the session would look recoverable while being unusable.
            tmp = d / "source.pdf.tmp"
            tmp.write_bytes(pdf_bytes)
            os.replace(tmp, d / "source.pdf")
        except OSError:
            log.warning("Could not persist source PDF for sid=%s", s.session_id, exc_info=True)
            pdf_path = None

        s.source_sha256 = sha
        vlm_store.save_session(
            session_factory,
            s,
            source_sha256=sha,
            source_path=str(pdf_path) if pdf_path else "",
        )
        return sha

    def _rehydrate(session_id: str) -> VlmIngestSession | None:
        """Rebuild an evicted session from durable state — the registry loader.

        Returns ``None`` only when the session genuinely does not exist: one
        that was deleted, or an id that was never issued.
        """
        state = vlm_store.load_session(session_factory, session_id)
        if state is None:
            return None

        pdf_bytes = b""
        src = state.get("source_path") or ""
        if src:
            try:
                pdf_bytes = Path(src).read_bytes()
            except OSError:
                log.warning(
                    "Source PDF missing for sid=%s at %s — rehydrating results only",
                    session_id, src,
                )

        s = vlm_store.rehydrate(state, pdf_bytes)
        s.checkpoint_dir = str(artifacts_dir / "vlm_sessions" / session_id)
        s.writer = vlm_store.LedgerSessionWriter(session_factory)
        s.source_sha256 = state.get("source_sha256", "")
        return s

    registry.set_loader(_rehydrate)

    def _cache_lookup(cache_key: str) -> dict[str, str] | None:
        with session_factory() as db:
            row = vlm_store.cache_lookup(db, cache_key)
            if row is None:
                return None
            return {"markdown": row.markdown, "model": row.model}

    def _cache_store(
        cache_key: str, sha: str, page_num: int, markdown: str, model: str, meta: dict,
    ) -> None:
        try:
            with session_factory() as db:
                vlm_store.cache_store(
                    db, cache_key=cache_key, source_sha256=sha, page_num=page_num,
                    markdown=markdown, model=model, meta=meta,
                )
                db.commit()
        except Exception:  # a cache write must never fail the job
            log.warning("VLM cache store failed for key=%s", cache_key[:12], exc_info=True)

    async def _extract_page(
        s: VlmIngestSession,
        page_num: int,
        page_settings: Any,
        resolved: Any,
        provider: Any,
    ) -> PageResult:
        """Return one page's extraction, consulting the content-addressed cache.

        The cache key covers the source document, page, render settings, prompt
        and model — everything that determines the output — so a hit is always
        safe to serve. This is what makes a re-run after any failure cost only
        the page that was in flight rather than the whole job.
        """
        crop_tuple = (
            page_settings.crop_top,
            page_settings.crop_bottom,
            page_settings.crop_left,
            page_settings.crop_right,
        )
        cache_key = ""
        if s.source_sha256:
            cache_key = vlm_store.compute_cache_key(
                source_sha256=s.source_sha256,
                page_num=page_num,
                dpi=page_settings.dpi,
                crop=crop_tuple,
                mask_regions=page_settings.mask_regions,
                system_prompt=s.config.system_prompt,
                model=resolved.model_name,
            )
            hit = await run_in_threadpool(_cache_lookup, cache_key)
            if hit is not None:
                log.info(
                    "VLM cache hit: sid=%s page=%d key=%s — no model call",
                    s.session_id, page_num, cache_key[:12],
                )
                return PageResult(
                    page_num=page_num,
                    markdown=hit["markdown"],
                    model=hit["model"],
                    dpi=page_settings.dpi,
                    crop_top=page_settings.crop_top,
                    crop_bottom=page_settings.crop_bottom,
                    cache_key=cache_key,
                )

        page_uri = await run_in_threadpool(
            render_page_base64, s.pdf_bytes, page_num, dpi=page_settings.dpi,
            crop=CropMargins(
                top=page_settings.crop_top,
                bottom=page_settings.crop_bottom,
                left=page_settings.crop_left,
                right=page_settings.crop_right,
            ),
            mask_regions=page_settings.mask_regions or None,
        )
        raw_messages = build_vision_messages(
            page_image_uri=page_uri,
            current_markdown="",  # no prior extraction — VLM produces from scratch
            system_prompt=s.config.system_prompt,
        )
        messages = [ChatMessage(role=m["role"], content=m["content"]) for m in raw_messages]

        log.info(
            "VLM ingest page: sid=%s page=%d model=%s dpi=%d",
            s.session_id, page_num, resolved.model_name, page_settings.dpi,
        )
        markdown = await provider.chat(
            model=resolved.model_name,
            messages=messages,
            params=resolved.params,
        )

        if cache_key:
            await run_in_threadpool(
                _cache_store, cache_key, s.source_sha256, page_num, markdown,
                resolved.model_name, {"dpi": page_settings.dpi, "crop": list(crop_tuple)},
            )

        return PageResult(
            page_num=page_num,
            markdown=markdown,
            model=resolved.model_name,
            dpi=page_settings.dpi,
            crop_top=page_settings.crop_top,
            crop_bottom=page_settings.crop_bottom,
            cache_key=cache_key,
        )

    def _find_source_pdf(run_id: int) -> tuple[bytes, str]:
        """Locate and read the source PDF for a run_id."""
        with session_factory() as session:
            run = get_workflow_run(session, run_id=run_id)
            if run is None:
                raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
            meta = run.meta or {}
            source_filename = meta.get("source_filename", "")
            source_mime = meta.get("source_mime_type", "")
            if "pdf" not in source_mime.lower() and not source_filename.lower().endswith(".pdf"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Run {run_id} source is not a PDF",
                )
            refs = list_artifact_refs(session, run_id=run_id)
            source_ref = None
            for ref in refs:
                if ref.kind == "source" or (ref.path and "/source/" in ref.path):
                    source_ref = ref
                    break
            if source_ref is None:
                raise HTTPException(status_code=404, detail=f"No source artifact for run {run_id}")
        source_path = artifacts_dir / source_ref.path
        if not source_path.exists():
            raise HTTPException(status_code=404, detail=f"Source file not found at {source_ref.path}")
        return source_path.read_bytes(), source_filename

    def _resolve_vision_model() -> tuple[Any, Any]:
        """Resolve the vision_model from effective config. Returns (resolved, provider)."""
        from atlas.config_versions import get_active_config_version
        yaml_defaults = config_manager.get()
        with session_factory() as session:
            active = get_active_config_version(session)
        if active is not None:
            models_cfg = active.payload.get("models", {}) or {}
        else:
            models_cfg = yaml_defaults.models
        model_registry = ModelRegistry(settings=settings, models_cfg=models_cfg)
        try:
            resolved = model_registry.resolve("vision_model")
        except KeyError:
            raise HTTPException(
                status_code=503,
                detail="No vision_model configured in models.yaml. "
                       "Add a vision_model role pointing to a multimodal model.",
            )
        provider = model_registry.provider_for(resolved.provider_name)
        return resolved, provider

    # ------------------------------------------------------------------
    # Start session
    # ------------------------------------------------------------------

    @r.post("/start", response_model=StartSessionResponse)
    async def start_session(req: StartSessionRequest) -> StartSessionResponse:
        """Start a VLM ingest session from an existing pipeline run."""
        pdf_bytes, filename = await run_in_threadpool(_find_source_pdf, req.run_id)
        n_pages = await run_in_threadpool(page_count, pdf_bytes)

        cfg = VlmIngestConfig.from_dict(req.config) if req.config else VlmIngestConfig()

        s = registry.create(
            pdf_bytes=pdf_bytes,
            page_count=n_pages,
            source_filename=filename,
            run_id=req.run_id,
            config=cfg,
            headless=req.headless,
        )
        _arm_session(s, pdf_bytes)
        log.info("VLM ingest session started: sid=%s run=%d pages=%d", s.session_id, req.run_id, n_pages)

        return StartSessionResponse(
            session_id=s.session_id,
            page_count=n_pages,
            source_filename=filename,
            status=s.status.value,
            headless=s.headless,
        )

    @r.post("/start-upload", response_model=StartSessionResponse)
    async def start_session_upload(
        file: UploadFile = File(...),
        config: str = Form("{}"),
        headless: bool = Form(False),
    ) -> StartSessionResponse:
        """Start a VLM ingest session from an uploaded PDF."""
        import json

        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Only PDF files are supported")

        pdf_bytes = await file.read()
        if len(pdf_bytes) > int(settings.atlas_pdf_max_bytes):
            raise HTTPException(
                status_code=413,
                detail=(
                    f"PDF exceeds size limit "
                    f"({len(pdf_bytes)} > {int(settings.atlas_pdf_max_bytes)} bytes)"
                ),
            )
        n_pages = await run_in_threadpool(page_count, pdf_bytes)

        try:
            cfg_dict = json.loads(config) if config else {}
        except json.JSONDecodeError:
            cfg_dict = {}
        cfg = VlmIngestConfig.from_dict(cfg_dict) if cfg_dict else VlmIngestConfig()

        s = registry.create(
            pdf_bytes=pdf_bytes,
            page_count=n_pages,
            source_filename=file.filename or "upload.pdf",
            config=cfg,
            headless=headless,
        )
        _arm_session(s, pdf_bytes)
        log.info("VLM ingest session started (upload): sid=%s pages=%d", s.session_id, n_pages)

        return StartSessionResponse(
            session_id=s.session_id,
            page_count=n_pages,
            source_filename=file.filename or "upload.pdf",
            status=s.status.value,
            headless=s.headless,
        )

    # ------------------------------------------------------------------
    # Session info
    # ------------------------------------------------------------------

    @r.get("/sessions", response_model=list[ResumableSession])
    async def list_sessions() -> list[dict[str, Any]]:
        """List VLM ingest sessions that can still be resumed.

        Sourced from the ledger, not the in-memory cache: a session the cache
        has released is every bit as resumable as one still held in RAM, and
        listing only the hot ones is what made sessions look "lost".
        """
        durable = await run_in_threadpool(vlm_store.list_sessions, session_factory)
        return durable

    @r.get("/{session_id}", response_model=SessionSummary)
    async def get_session(session_id: str) -> dict[str, Any]:
        """Get session status and progress."""
        s = _get_session(session_id)
        summary = s.summary()
        progress = summary.get("progress", {})
        _diag(
            "VLM session status: sid=%s status=%s done=%s pending=%s errors=%s",
            session_id,
            summary.get("status"),
            progress.get("done"),
            progress.get("pending"),
            progress.get("errors"),
        )
        return summary

    @r.delete("/{session_id}")
    async def delete_session(session_id: str) -> dict[str, str]:
        """Discard a session — the only way work is intentionally destroyed.

        Removes both the cached copy and the durable record. The page *cache*
        survives, so re-ingesting the same document stays free.
        """
        cached = registry.delete(session_id)
        durable = await run_in_threadpool(vlm_store.delete_session, session_factory, session_id)

        # Reclaim the checkpoint directory too — the source PDF plus one file
        # per page. Leaving these behind meant artifacts/vlm_sessions/ grew
        # without bound across every discarded job.
        def _purge_dir() -> None:
            import shutil

            d = artifacts_dir / "vlm_sessions" / session_id
            # Guard against a session_id that could escape the parent.
            if d.parent == artifacts_dir / "vlm_sessions" and d.is_dir():
                shutil.rmtree(d, ignore_errors=True)

        await run_in_threadpool(_purge_dir)

        if cached or durable:
            log.info("VLM session deleted by operator: sid=%s", session_id)
            return {"message": f"Session {session_id} deleted"}
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @r.post("/{session_id}/config", response_model=SessionSummary)
    async def update_config(session_id: str, req: UpdateConfigRequest) -> dict[str, Any]:
        """Update global and/or per-page settings."""
        s = _get_session(session_id)

        # Update globals
        if req.dpi is not None:
            s.config.dpi = req.dpi
        if req.crop_top is not None:
            s.config.crop_top = req.crop_top
        if req.crop_bottom is not None:
            s.config.crop_bottom = req.crop_bottom
        if req.crop_left is not None:
            s.config.crop_left = req.crop_left
        if req.crop_right is not None:
            s.config.crop_right = req.crop_right
        if req.system_prompt is not None:
            s.config.system_prompt = req.system_prompt

        # Apply per-page overrides
        if req.page_overrides:
            for po in req.page_overrides:
                overrides: dict[str, Any] = {}
                if po.enabled is not None:
                    overrides["enabled"] = po.enabled
                if po.dpi is not None:
                    overrides["dpi"] = po.dpi
                if po.crop_top is not None:
                    overrides["crop_top"] = po.crop_top
                if po.crop_bottom is not None:
                    overrides["crop_bottom"] = po.crop_bottom
                if po.crop_left is not None:
                    overrides["crop_left"] = po.crop_left
                if po.crop_right is not None:
                    overrides["crop_right"] = po.crop_right
                if po.mask_regions is not None:
                    overrides["mask_regions"] = po.mask_regions
                if overrides:
                    s.update_page_settings(po.page_num, **overrides)

        _diag(
            "VLM config updated: sid=%s dpi=%s crop=(%.3f,%.3f,%.3f,%.3f) overrides=%d",
            session_id,
            s.config.dpi,
            s.config.crop_top,
            s.config.crop_bottom,
            s.config.crop_left,
            s.config.crop_right,
            len(req.page_overrides or []),
        )

        # Config is durable state — it feeds the page cache key, so a resumed
        # session that lost its overrides would re-run pages at the wrong
        # settings and miss every cache entry.
        await run_in_threadpool(vlm_store.save_session, session_factory, s)

        return s.summary()

    @r.get("/{session_id}/export-config", response_model=ExportConfigResponse)
    async def export_config(session_id: str) -> ExportConfigResponse:
        """Export session config for headless reuse on other documents."""
        s = _get_session(session_id)
        return ExportConfigResponse(
            config=s.config.to_dict(),
            source_filename=s.source_filename,
            page_count=s.page_count,
        )

    # ------------------------------------------------------------------
    # Page analysis
    # ------------------------------------------------------------------

    @r.get("/{session_id}/page-analysis")
    async def get_page_analysis(session_id: str) -> dict[str, Any]:
        """Analyze all pages for content classification (text-native / image-heavy / image-only).

        Results are cached on the session so repeated calls are fast.
        Returns a dict of page_num -> analysis result.
        """
        s = _get_session(session_id)

        if not s.page_analysis:
            for p in range(s.page_count):
                try:
                    analysis = await run_in_threadpool(analyze_page, s.pdf_bytes, p)
                    s.page_analysis[p] = analysis
                except Exception as exc:
                    s.page_analysis[p] = {
                        "content_class": "unknown",
                        "text_chars": 0,
                        "image_ratio": 0.0,
                        "image_rects": [],
                        "error": str(exc),
                    }

        return {"pages": s.page_analysis}

    # ------------------------------------------------------------------
    # Thumbnails / preview
    # ------------------------------------------------------------------

    @r.get("/{session_id}/thumbnails")
    async def get_thumbnails(
        session_id: str,
        dpi: int = 72,
        limit: int = Query(default=200, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        """Render page thumbnails and return a page of base64 PNGs with pagination metadata.

        ``limit`` / ``offset`` page over the session's pages. ``total`` is the
        session page count and ``has_more`` tells the caller whether another page
        is available. Response shape is an object: ``{"pages": [...], "total": N,
        "offset": O, "limit": L, "has_more": bool}``.
        """
        import json
        import time
        s = _get_session(session_id)
        thumbs: list[dict[str, Any]] = []
        started = time.perf_counter()
        error_count = 0

        total = s.page_count
        start = min(offset, total)
        end = min(offset + limit, total)

        for p in range(start, end):
            settings = s.config.settings_for_page(p)
            try:
                png = await run_in_threadpool(
                    render_page, s.pdf_bytes, p, dpi=dpi,
                )
                import base64
                b64 = base64.b64encode(png).decode("ascii")
                thumbs.append({
                    "page_num": p,
                    "thumbnail": f"data:image/png;base64,{b64}",
                    "enabled": settings.enabled,
                    "status": s.page_statuses.get(p, PageStatus.PENDING).value,
                })
            except Exception as exc:
                thumbs.append({
                    "page_num": p,
                    "thumbnail": None,
                    "enabled": settings.enabled,
                    "status": "error",
                    "error": str(exc),
                })
                error_count += 1

        payload = json.dumps(thumbs)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        _diag(
            "VLM thumbnails: sid=%s pages=%d dpi=%d errors=%d elapsed_ms=%.1f payload_kb=%.1f",
            session_id,
            len(thumbs),
            dpi,
            error_count,
            elapsed_ms,
            len(payload.encode("utf-8")) / 1024.0,
        )

        return {
            "pages": thumbs,
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(thumbs) < total,
        }

    @r.get("/{session_id}/preview/{page_num}")
    async def preview_page(
        session_id: str,
        page_num: int,
        dpi: int | None = None,
        crop_top: float | None = None,
        crop_bottom: float | None = None,
        crop_left: float | None = None,
        crop_right: float | None = None,
        apply_crop: bool = False,
        apply_masks: bool = False,
    ) -> Response:
        """Render a single page preview.

        Defaults to the session config, but callers can provide temporary override values
        for visual tuning in the UI.
        """
        s = _get_session(session_id)
        if page_num < 0 or page_num >= s.page_count:
            raise HTTPException(status_code=400, detail=f"Page {page_num} out of range")

        settings = s.config.settings_for_page(page_num)
        effective_dpi = int(dpi if dpi is not None else settings.dpi)
        crop = CropMargins(
            top=float(crop_top if crop_top is not None else settings.crop_top),
            bottom=float(crop_bottom if crop_bottom is not None else settings.crop_bottom),
            left=float(crop_left if crop_left is not None else settings.crop_left),
            right=float(crop_right if crop_right is not None else settings.crop_right),
        )
        png_bytes = await run_in_threadpool(
            render_page,
            s.pdf_bytes,
            page_num,
            dpi=effective_dpi,
            crop=crop if apply_crop else None,
            mask_regions=settings.mask_regions if apply_masks else None,
        )
        _diag(
            "VLM preview: sid=%s page=%d dpi=%d apply_crop=%s crop=(%.3f,%.3f,%.3f,%.3f)",
            session_id,
            page_num,
            effective_dpi,
            apply_crop,
            crop.top,
            crop.bottom,
            crop.left,
            crop.right,
        )
        return Response(content=png_bytes, media_type="image/png")

    # ------------------------------------------------------------------
    # Process page (VLM call — one at a time)
    # ------------------------------------------------------------------

    @r.post("/{session_id}/process-page", response_model=ProcessPageResponse)
    async def process_page(session_id: str, req: ProcessPageRequest) -> ProcessPageResponse:
        """Process a single page through the VLM.

        If ``page_num`` is omitted, auto-picks the next pending page.
        Only one page is processed per call to prevent model overload.
        Returns HTTP 409 if the requested page is already being processed by
        a concurrent request.  Returns HTTP 200 with ``status="error"`` and
        an ``error`` field when the VLM provider or render step fails.
        """
        s = _get_session(session_id)

        # Determine which page to process
        p = req.page_num
        if p is None:
            p = s.next_pending_page()
            if p is None:
                raise HTTPException(
                    status_code=400,
                    detail="No pending pages left. All enabled pages are done or skipped.",
                )
        if p < 0 or p >= s.page_count:
            raise HTTPException(status_code=400, detail=f"Page {p} out of range")

        # CAS guard: reject duplicate concurrent requests for the same page.
        # No await occurs between the check and the assignment, so this is
        # atomic within asyncio's single-threaded event loop.
        if s.page_statuses.get(p) == PageStatus.PROCESSING:
            raise HTTPException(
                status_code=409,
                detail=f"Page {p} is already being processed. Try again when it completes.",
            )

        settings = s.config.settings_for_page(p)
        if not settings.enabled:
            s.skip_page(p)
            return ProcessPageResponse(
                page_num=p,
                markdown="",
                model="",
                status="skipped",
            )

        # Mark the *page* in flight. The session status is deliberately left
        # alone: a single interactive page is not a bulk run, and marking the
        # session busy here is what used to wedge it permanently.
        s.page_statuses[p] = PageStatus.PROCESSING

        try:
            resolved, provider = _resolve_vision_model()
            result = await _extract_page(s, p, settings, resolved, provider)
            s.set_page_result(p, result)

            return ProcessPageResponse(
                page_num=p,
                markdown=result.markdown,
                model=result.model,
                status="done",
            )

        except HTTPException:
            s.set_page_error(p, "VLM configuration error")
            raise
        except Exception as exc:
            error_msg = str(exc)
            log.error("VLM ingest failed for page %d: %s", p, error_msg, exc_info=True)
            s.set_page_error(p, error_msg)
            return ProcessPageResponse(
                page_num=p,
                markdown="",
                model="",
                status="error",
                error=error_msg,
            )
        finally:
            # No exit path may leave the page stuck in PROCESSING — that would
            # make the page permanently unreprocessable via the CAS guard above.
            if s.page_statuses.get(p) is PageStatus.PROCESSING:
                s.page_statuses[p] = PageStatus.PENDING

    # ------------------------------------------------------------------
    # Bulk process all pages (server-side sequential)
    # ------------------------------------------------------------------

    @r.post("/{session_id}/process-all", response_model=ProcessAllResponse)
    async def process_all_pages(session_id: str) -> ProcessAllResponse:
        """Process ALL pending enabled pages sequentially, then auto-stitch.

        Pages are processed one at a time to prevent VLM hallucination.
        When all pages are done the result is automatically stitched.
        Poll ``GET /{session_id}`` for progress during processing.
        """
        s = _get_session(session_id)
        if s.bulk_active:
            # A bulk loop is already running *in this process*; a second one
            # would double-process pages and double-spend VLM tokens. This is
            # an in-memory lock on purpose — a restart releases it, because
            # after a restart no loop is in fact running.
            raise HTTPException(
                status_code=409,
                detail="Session is already processing — poll GET /{session_id} for progress",
            )
        resolved, provider = _resolve_vision_model()
        s.bulk_active = True
        s.set_status(SessionStatus.PROCESSING)

        processed = 0
        skipped = 0
        failed = 0
        cancelled = False
        errors: dict[int, str] = {}

        stitch_resp: StitchResponse | None = None
        try:
            for p in s.enabled_pages():
                # Cancellation: discarding the session (DELETE) removes it
                # from the registry and flags the object. Without this check
                # the loop would keep calling the VLM for hours on a document
                # nobody wants any more.
                if s.discarded or registry.get(session_id) is None:
                    cancelled = True
                    log.info(
                        "VLM bulk cancelled (session discarded): sid=%s at page=%d",
                        session_id, p,
                    )
                    break

                if s.page_statuses.get(p) in (PageStatus.DONE, PageStatus.SKIPPED):
                    continue  # already finished

                settings = s.config.settings_for_page(p)
                if not settings.enabled:
                    s.skip_page(p)
                    skipped += 1
                    continue

                s.page_statuses[p] = PageStatus.PROCESSING

                try:
                    log.info(
                        "VLM bulk page: sid=%s page=%d/%d model=%s",
                        session_id, p, s.page_count, resolved.model_name,
                    )
                    result = await _extract_page(s, p, settings, resolved, provider)
                    s.set_page_result(p, result)
                    processed += 1

                except Exception as exc:
                    error_msg = str(exc)
                    log.error("VLM bulk page %d failed: %s", p, error_msg, exc_info=True)
                    s.set_page_error(p, error_msg)
                    errors[p] = error_msg
                    failed += 1

                finally:
                    # A page must never be left claiming to be in flight.
                    if s.page_statuses.get(p) is PageStatus.PROCESSING:
                        s.page_statuses[p] = PageStatus.PENDING

            # Auto-stitch if any pages succeeded
            if not cancelled and any(
                st == PageStatus.DONE for st in s.page_statuses.values()
            ):
                s.set_status(SessionStatus.STITCHING)
                sr = s.stitch()
                stitch_resp = StitchResponse(
                    markdown=sr.markdown,
                    page_count=sr.page_count,
                    pages_processed=sr.pages_processed,
                    duplicate_lines_removed=sr.duplicate_lines_removed,
                    tables_merged=sr.tables_merged,
                    headings_merged=sr.headings_merged,
                )
            elif failed and not cancelled:
                s.status = SessionStatus.FAILED

        finally:
            # Releasing the lock and settling the status belong here, not on
            # the happy path: an exception escaping the loop used to leave the
            # session permanently "processing" and unstartable. A discarded
            # session is left alone — persisting it would recreate the rows
            # DELETE just removed.
            s.bulk_active = False
            if not s.discarded:
                s.settle()

        log.info(
            "VLM bulk complete: sid=%s processed=%d skipped=%d failed=%d cancelled=%s",
            session_id, processed, skipped, failed, cancelled,
        )

        return ProcessAllResponse(
            pages_processed=processed,
            pages_skipped=skipped,
            pages_failed=failed,
            errors=errors,
            stitch=stitch_resp,
        )

    # ------------------------------------------------------------------
    # Stitch + commit
    # ------------------------------------------------------------------

    @r.post("/{session_id}/stitch", response_model=StitchResponse)
    async def stitch_session(session_id: str) -> StitchResponse:
        """Stitch all completed pages into a single markdown document."""
        s = _get_session(session_id)

        if not any(
            st == PageStatus.DONE
            for st in s.page_statuses.values()
        ):
            raise HTTPException(
                status_code=400,
                detail="No pages have been processed yet.",
            )

        s.set_status(SessionStatus.STITCHING)
        result = s.stitch()

        return StitchResponse(
            markdown=result.markdown,
            page_count=result.page_count,
            pages_processed=result.pages_processed,
            duplicate_lines_removed=result.duplicate_lines_removed,
            tables_merged=result.tables_merged,
            headings_merged=result.headings_merged,
        )

    @r.post("/{session_id}/commit", response_model=CommitResponse)
    async def commit_session(session_id: str, req: CommitRequest) -> CommitResponse:
        """Save the stitched markdown as an artifact, then optionally feed
        through the pipeline to chunk, embed, and upsert to Qdrant.

        When ``feed_pipeline`` is True (the default), the committed markdown
        is pushed through the ingest pipeline with ``is_hitl_resume=True``
        so cleanup/judge/refine are skipped — the human already approved
        the content during VLM review.

        If the pipeline step fails the commit itself is still considered
        successful (the artifact is saved).  The response will include a
        non-empty ``warnings`` list describing the failure so the caller can
        surface it to the operator rather than silently discarding it.
        """
        import hashlib

        s = _get_session(session_id)
        md = req.markdown
        if md is None:
            if s.stitched is None:
                raise HTTPException(
                    status_code=400,
                    detail="No stitched result available. Call /stitch first or provide markdown.",
                )
            md = s.stitched.markdown

        # ── Resolve scope ────────────────────────────────────────────
        tenant_id = (req.tenant_id or settings.atlas_default_tenant_id).strip()
        project_id = (req.project_id or settings.atlas_default_project_id).strip()
        corpus_id = (req.corpus_id or settings.atlas_default_corpus_id).strip()
        doc_id = s.source_filename
        doc_version = "1"

        # For run-based sessions, inherit scope from the existing run.
        if s.run_id is not None:
            with session_factory() as db_session:
                existing_run = get_workflow_run(db_session, run_id=s.run_id)
                if existing_run is not None:
                    tenant_id = existing_run.tenant_id or tenant_id
                    project_id = existing_run.project_id or project_id
                    doc_id = existing_run.doc_id or doc_id
                    doc_version = existing_run.doc_version or doc_version
                    meta = existing_run.meta or {}
                    corpus_id = meta.get("corpus_id", corpus_id)

        # ── Ensure a WorkflowRun exists ──────────────────────────────
        run_id = s.run_id
        if run_id is None:
            # Create a workflow run so there's somewhere to save artifacts
            with session_factory() as db_session:
                wf_run = create_workflow_run(
                    db_session,
                    req=WorkflowRunCreateRequest(
                        tenant_id=tenant_id,
                        project_id=project_id,
                        doc_id=doc_id,
                        doc_version=doc_version,
                        status="complete",
                        current_node="vlm_ingest",
                        meta={
                            "source": "vlm_ingest_upload",
                            "session_id": s.session_id,
                            "page_count": s.page_count,
                            "corpus_id": corpus_id,
                        },
                    ),
                )
                run_id = wf_run.id

                # Save the source PDF as an artifact
                src_dir = artifacts_dir / f"runs/{run_id}"
                src_dir.mkdir(parents=True, exist_ok=True)
                pdf_path = src_dir / s.source_filename
                pdf_path.write_bytes(s.pdf_bytes)
                add_artifact_ref(
                    db_session,
                    run_id=run_id,
                    req=ArtifactRefCreateRequest(
                        kind="source",
                        path=f"runs/{run_id}/{s.source_filename}",
                        mime_type="application/pdf",
                        meta={"source": "vlm_ingest_upload"},
                    ),
                )

            s.run_id = run_id
            log.info("Auto-created workflow run %d for upload session %s", run_id, s.session_id)

        # ── Save VLM markdown artifact ───────────────────────────────
        save_dir = artifacts_dir / f"runs/{run_id}/vlm_ingest"
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / "markdown.md"
        save_path.write_text(md, encoding="utf-8")

        rel_path = f"runs/{run_id}/vlm_ingest/markdown.md"
        sha = hashlib.sha256(md.encode("utf-8")).hexdigest()

        # Record artifact ref
        with session_factory() as db_session:
            add_artifact_ref(
                db_session,
                run_id=run_id,
                req=ArtifactRefCreateRequest(
                    kind="vlm_ingest",
                    path=rel_path,
                    sha256=sha,
                    mime_type="text/markdown",
                    meta={
                        "source": "vlm_ingest",
                        "chars": len(md),
                        "session_id": s.session_id,
                        "config": s.config.to_dict(),
                        "pages_processed": s.stitched.pages_processed if s.stitched else 0,
                    },
                ),
            )

        # Also save the config for headless reuse
        import json
        config_path = save_dir / "config.json"
        config_path.write_text(json.dumps(s.config.to_dict(), indent=2), encoding="utf-8")

        s.set_status(SessionStatus.COMMITTED)
        log.info("VLM ingest committed: sid=%s run=%d path=%s chars=%d", s.session_id, run_id, rel_path, len(md))

        # ── Feed through pipeline (chunk → embed → Qdrant) ──────────
        chunks_upserted = 0
        pipeline_warnings: list[str] = []
        if req.feed_pipeline:
            try:
                pipeline_result = await ingest_text_via_pipeline(
                    config_manager=config_manager,
                    session_factory=session_factory,
                    existing_run_id=run_id,
                    doc_id=doc_id,
                    doc_version=doc_version,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    corpus_id=corpus_id,
                    text=md,
                    source_mime_type="text/markdown",
                    is_finalized=True,
                    is_sensitive=False,
                    metadata={
                        "source": "vlm_ingest",
                        "session_id": s.session_id,
                        "vlm_config": s.config.to_dict(),
                    },
                    is_hitl_resume=True,
                )
                chunks_upserted = pipeline_result.get("chunks_upserted", 0)
                log.info(
                    "VLM ingest pipeline complete: run=%d chunks=%d",
                    run_id, chunks_upserted,
                )
            except Exception as exc:
                warn = f"Pipeline processing failed: {exc}"
                log.exception("Pipeline processing failed for VLM commit run=%d", run_id)
                pipeline_warnings.append(warn)
                # Mark the run failed so it doesn't sit in 'running' forever —
                # a run whose pipeline feed died has no chunks and would only
                # surface later as a dangling-run maintenance finding.
                try:
                    from sqlalchemy import select as _select

                    from atlas.models import WorkflowRun as _WorkflowRun

                    with session_factory() as _session:
                        _w = _session.execute(
                            _select(_WorkflowRun).where(_WorkflowRun.id == run_id)
                        ).scalars().first()
                        if _w is not None:
                            _w.status = "failed"
                            _w.error_message = warn[:500]
                            _session.commit()
                except Exception:
                    log.warning("Could not mark run %d failed after pipeline error", run_id, exc_info=True)

        msg = "VLM ingest committed"
        if chunks_upserted:
            msg += f" — {chunks_upserted} chunks indexed"
        elif pipeline_warnings:
            msg += " — artifact saved but pipeline processing failed (see warnings)"
        else:
            msg += " successfully"

        return CommitResponse(
            run_id=run_id,
            path=rel_path,
            chars=len(md),
            chunks_upserted=chunks_upserted,
            message=msg,
            warnings=pipeline_warnings if pipeline_warnings else None,
        )

    # ------------------------------------------------------------------
    # Page-level result access (for reviewing individual pages)
    # ------------------------------------------------------------------

    @r.get("/{session_id}/page-result/{page_num}")
    async def get_page_result(session_id: str, page_num: int) -> dict[str, Any]:
        """Get the VLM result for a specific page."""
        s = _get_session(session_id)
        status = s.page_statuses.get(page_num, PageStatus.PENDING)
        result: dict[str, Any] = {
            "page_num": page_num,
            "status": status.value,
        }
        if page_num in s.page_results:
            pr = s.page_results[page_num]
            result["markdown"] = pr.markdown
            result["model"] = pr.model
            result["dpi"] = pr.dpi
        if page_num in s.page_errors:
            result["error"] = s.page_errors[page_num]

        settings = s.config.settings_for_page(page_num)
        result["settings"] = {
            "enabled": settings.enabled,
            "dpi": settings.dpi,
            "crop_top": settings.crop_top,
            "crop_bottom": settings.crop_bottom,
            "crop_left": settings.crop_left,
            "crop_right": settings.crop_right,
        }
        return result

    @r.put("/{session_id}/page-result/{page_num}")
    async def update_page_result(
        session_id: str,
        page_num: int,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """Update the VLM result for a page (operator correction)."""
        s = _get_session(session_id)
        if page_num not in s.page_results:
            raise HTTPException(
                status_code=404,
                detail=f"No result for page {page_num}. Process it first.",
            )
        pr = s.page_results[page_num]
        if "markdown" in body:
            # Route through set_page_result so the correction is written to the
            # ledger and the checkpoint file. Assigning page_results directly
            # kept the edit in memory only, so an operator's hand-corrections —
            # more expensive than the VLM output they replace — were silently
            # discarded when the session was released from the cache.
            #
            # cache_key is deliberately dropped: a human edit is not an
            # extraction of those inputs and must never be served as one.
            s.set_page_result(
                page_num,
                PageResult(
                    page_num=pr.page_num,
                    markdown=body["markdown"],
                    model=pr.model,
                    dpi=pr.dpi,
                    crop_top=pr.crop_top,
                    crop_bottom=pr.crop_bottom,
                ),
            )
        return {"page_num": page_num, "status": "updated"}

    # Expose the registry for maintenance access (e.g. periodic eviction).
    r._vlm_session_registry = registry  # type: ignore[attr-defined]
    return r
