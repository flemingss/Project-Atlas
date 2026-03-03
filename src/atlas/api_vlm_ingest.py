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

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import sessionmaker
from starlette.concurrency import run_in_threadpool

from atlas.auth import require_admin_token
from atlas.config_manager import ConfigManager
from atlas.ingest.page_renderer import (
    CropMargins,
    build_vision_messages,
    page_count,
    render_page,
    render_page_base64,
)
from atlas.llm.provider import ChatMessage
from atlas.llm.registry import ModelRegistry
from atlas.settings import Settings
from atlas.vlm_ingest.session import (
    PageStatus,
    SessionStatus,
    VlmIngestConfig,
    VlmIngestSession,
    SessionRegistry,
)
from atlas.vlm_ingest.stitcher import PageResult
from atlas.workflow_ledger import (
    add_artifact_ref,
    ArtifactRefCreateRequest,
    create_workflow_run,
    get_workflow_run,
    list_artifact_refs,
    WorkflowRunCreateRequest,
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


class PageSettingsUpdate(BaseModel):
    """Per-page setting overrides."""
    page_num: int
    enabled: bool | None = None
    dpi: int | None = None
    crop_top: float | None = None
    crop_bottom: float | None = None
    crop_left: float | None = None
    crop_right: float | None = None


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
    finish_reason: str | None = None


class StitchResponse(BaseModel):
    markdown: str
    page_count: int
    pages_processed: int
    duplicate_lines_removed: int
    tables_merged: int
    headings_merged: int


class CommitRequest(BaseModel):
    """Commit stitched markdown — save as artifact and optionally feed into pipeline."""
    markdown: str | None = None  # None = use stitched result
    feed_pipeline: bool = False  # future: trigger cleanup → judge → etc.


class CommitResponse(BaseModel):
    run_id: int | None
    path: str
    chars: int
    message: str


class SessionSummary(BaseModel):
    session_id: str
    status: str
    source_filename: str
    run_id: int | None
    page_count: int
    headless: bool
    progress: dict[str, int]
    config: dict[str, Any]
    page_statuses: dict[str, str] = {}


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
            raise HTTPException(status_code=404, detail=f"Session '{sid}' not found or expired")
        return s

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
        log.info("VLM ingest session started: sid=%s run=%d pages=%d", s.session_id, req.run_id, n_pages)

        return StartSessionResponse(
            session_id=s.session_id,
            page_count=n_pages,
            source_filename=filename,
            status=s.status.value,
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
        log.info("VLM ingest session started (upload): sid=%s pages=%d", s.session_id, n_pages)

        return StartSessionResponse(
            session_id=s.session_id,
            page_count=n_pages,
            source_filename=file.filename or "upload.pdf",
            status=s.status.value,
        )

    # ------------------------------------------------------------------
    # Session info
    # ------------------------------------------------------------------

    @r.get("/sessions", response_model=list[SessionSummary])
    async def list_sessions() -> list[dict[str, Any]]:
        """List all active VLM ingest sessions."""
        return registry.list_sessions()

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
        """Discard a session."""
        if registry.delete(session_id):
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
    # Thumbnails / preview
    # ------------------------------------------------------------------

    @r.get("/{session_id}/thumbnails")
    async def get_thumbnails(session_id: str, dpi: int = 72) -> Response:
        """Render all pages as low-res thumbnails and return as a JSON array of base64 PNGs."""
        import json
        import time
        s = _get_session(session_id)
        thumbs: list[dict[str, Any]] = []
        started = time.perf_counter()
        error_count = 0

        for p in range(s.page_count):
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
            s.page_count,
            dpi,
            error_count,
            elapsed_ms,
            len(payload.encode("utf-8")) / 1024.0,
        )

        return Response(
            content=payload,
            media_type="application/json",
        )

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

        # Mark processing
        s.page_statuses[p] = PageStatus.PROCESSING
        s.status = SessionStatus.PROCESSING

        settings = s.config.settings_for_page(p)
        if not settings.enabled:
            s.skip_page(p)
            return ProcessPageResponse(
                page_num=p,
                markdown="",
                model="",
                status="skipped",
            )

        try:
            resolved, provider = _resolve_vision_model()

            # Render page
            crop = CropMargins(
                top=settings.crop_top,
                bottom=settings.crop_bottom,
                left=settings.crop_left,
                right=settings.crop_right,
            )
            page_uri = await run_in_threadpool(
                render_page_base64, s.pdf_bytes, p, dpi=settings.dpi, crop=crop,
            )

            # Build VLM prompt — page in isolation, no cross-page context
            raw_messages = build_vision_messages(
                page_image_uri=page_uri,
                current_markdown="",  # no prior extraction — VLM produces from scratch
                system_prompt=s.config.system_prompt,
            )
            messages = [ChatMessage(role=m["role"], content=m["content"]) for m in raw_messages]

            log.info(
                "VLM ingest page: sid=%s page=%d model=%s dpi=%d",
                session_id, p, resolved.model_name, settings.dpi,
            )

            corrected = await provider.chat(
                model=resolved.model_name,
                messages=messages,
                params=resolved.params,
            )

            result = PageResult(
                page_num=p,
                markdown=corrected,
                model=resolved.model_name,
                dpi=settings.dpi,
                crop_top=settings.crop_top,
                crop_bottom=settings.crop_bottom,
            )
            s.set_page_result(p, result)

            return ProcessPageResponse(
                page_num=p,
                markdown=corrected,
                model=resolved.model_name,
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
                finish_reason=error_msg,
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

        s.status = SessionStatus.STITCHING
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
        """Save the stitched markdown as an artifact.

        Optionally uses the session's stitched result or accepts explicit markdown.
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

        # Determine where to save — auto-create a run for uploaded PDFs
        run_id = s.run_id
        if run_id is None:
            # Create a workflow run so there's somewhere to save artifacts
            with session_factory() as db_session:
                wf_run = create_workflow_run(
                    db_session,
                    req=WorkflowRunCreateRequest(
                        tenant_id="default",
                        project_id="vlm-ingest",
                        doc_id=s.source_filename,
                        doc_version="1",
                        status="complete",
                        current_node="vlm_ingest",
                        meta={
                            "source": "vlm_ingest_upload",
                            "session_id": s.session_id,
                            "page_count": s.page_count,
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

        s.status = SessionStatus.COMMITTED
        log.info("VLM ingest committed: sid=%s run=%d path=%s chars=%d", s.session_id, run_id, rel_path, len(md))

        return CommitResponse(
            run_id=run_id,
            path=rel_path,
            chars=len(md),
            message="VLM ingest committed successfully",
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
            s.page_results[page_num] = PageResult(
                page_num=pr.page_num,
                markdown=body["markdown"],
                model=pr.model,
                dpi=pr.dpi,
                crop_top=pr.crop_top,
                crop_bottom=pr.crop_bottom,
            )
        return {"page_num": page_num, "status": "updated"}

    return r
