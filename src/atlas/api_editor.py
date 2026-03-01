"""Editor API endpoints — Document Editor backend.

Serves the editor page and provides endpoints for:
- VLM-powered page-level markdown correction
- Page rendering (PDF → PNG)
- Run/artifact retrieval for the editor

All endpoints are mounted under ``/editor`` and ``/api/editor``.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
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
from atlas.workflow_ledger import (
    get_latest_run_by_doc_id,
    get_workflow_run,
    list_artifact_refs,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class VisionRefineRequest(BaseModel):
    """Request body for ``POST /api/editor/vision-refine``."""
    run_id: int
    page_num: int = 0
    current_markdown: str
    dpi: int = 200
    crop_top: float = 0.04
    crop_bottom: float = 0.04
    system_prompt: str | None = None


class VisionRefineResponse(BaseModel):
    """Response from VLM vision-refine."""
    corrected_markdown: str
    model: str
    page_num: int
    finish_reason: str | None = None


class PageRenderRequest(BaseModel):
    """Request body for ``POST /api/editor/render-page``."""
    run_id: int
    page_num: int = 0
    dpi: int = 200
    crop_top: float = 0.0
    crop_bottom: float = 0.0


class PageInfoResponse(BaseModel):
    """Response from page-info endpoint."""
    run_id: int
    page_count: int
    source_filename: str
    source_mime_type: str


class DocResolveResponse(BaseModel):
    """Response from ``GET /api/editor/resolve-doc/{doc_id}``."""
    doc_id: str
    run_id: int
    doc_version: str
    status: str
    source_filename: str


class SaveMarkdownRequest(BaseModel):
    """Request body for ``POST /api/editor/save-markdown``."""
    run_id: int
    markdown: str


class SaveMarkdownResponse(BaseModel):
    """Response from save-markdown endpoint."""
    run_id: int
    path: str
    chars: int
    message: str


class LlmRefineRequest(BaseModel):
    """Request body for ``POST /api/editor/llm-refine``."""
    run_id: int
    markdown: str


class LlmRefineResponse(BaseModel):
    """Response from LLM refine endpoint."""
    refined_markdown: str
    model: str
    success: bool
    improvements: list[str]


class ReJudgeRequest(BaseModel):
    """Request body for ``POST /api/editor/re-judge``."""
    run_id: int
    markdown: str
    judge_cutoff: int = 4


class ReJudgeResponse(BaseModel):
    """Response from re-judge endpoint."""
    score: int
    sub_scores: dict[str, int]
    rationale: str
    needs_refinement: bool
    model: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_source_pdf(
    session_factory: sessionmaker,
    artifacts_dir: Path,
    run_id: int,
) -> tuple[bytes, str]:
    """Locate and read the source PDF for a given run_id.

    Returns (pdf_bytes, filename).
    Raises HTTPException 404 if not found.
    """
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
                detail=f"Run {run_id} source is not a PDF (mime={source_mime}, file={source_filename})",
            )

        # Find source artifact
        refs = list_artifact_refs(session, run_id=run_id)
        source_ref = None
        for ref in refs:
            if ref.kind == "source" or (ref.path and "/source/" in ref.path):
                source_ref = ref
                break

        if source_ref is None:
            raise HTTPException(status_code=404, detail=f"No source artifact found for run {run_id}")

    # Read the PDF bytes
    source_path = artifacts_dir / source_ref.path
    if not source_path.exists():
        raise HTTPException(status_code=404, detail=f"Source file not found at {source_ref.path}")

    return source_path.read_bytes(), source_filename


def _find_current_markdown(
    session_factory: sessionmaker,
    artifacts_dir: Path,
    run_id: int,
) -> str | None:
    """Find the latest markdown artifact for a run.

    Searches for ingest/markdown.md or the latest markdown artifact.
    Returns None if not found.
    """
    with session_factory() as session:
        refs = list_artifact_refs(session, run_id=run_id)

    # Look for markdown artifacts (prefer refined > cleanup > ingest)
    md_ref = None
    for ref in reversed(refs):  # latest first
        if ref.path and ref.path.endswith(".md"):
            md_ref = ref
            break

    if md_ref is None:
        return None

    md_path = artifacts_dir / md_ref.path
    if not md_path.exists():
        return None

    return md_path.read_text(encoding="utf-8")


def _extract_page_texts_from_docling(
    session_factory: sessionmaker,
    artifacts_dir: Path,
    run_id: int,
) -> dict[int, list[str]]:
    """Parse Docling JSON and group text content by page number.

    Returns ``{page_no: [text_str, ...]}`` for all pages found.
    Each text string is the raw ``text`` field from Docling elements.
    Tables are rendered as their ``text`` if present, otherwise data
    cells are joined.
    """
    import json

    with session_factory() as session:
        refs = list_artifact_refs(session, run_id=run_id)

    docling_ref = None
    for ref in refs:
        if ref.path and ref.path.endswith("docling.json"):
            docling_ref = ref
            break

    if docling_ref is None:
        return {}

    docling_path = artifacts_dir / docling_ref.path
    if not docling_path.exists():
        return {}

    with open(docling_path, encoding="utf-8") as f:
        doc = json.load(f)

    # Build a lookup from $ref → element
    ref_lookup: dict[str, dict] = {}
    for collection_name in ("texts", "tables", "pictures", "groups", "key_value_items"):
        for item in doc.get(collection_name, []):
            self_ref = item.get("self_ref")
            if self_ref:
                ref_lookup[self_ref] = item

    # Walk body children in order, resolve refs, group by page
    pages: dict[int, list[str]] = {}
    body = doc.get("body", {})
    for child in body.get("children", []):
        child_ref = child.get("$ref")
        if not child_ref:
            continue
        elem = ref_lookup.get(child_ref)
        if not elem:
            continue

        # Determine page number from provenance
        prov = elem.get("prov", [])
        page_no = prov[0].get("page_no", 0) if prov else 0

        # Get text content
        text = elem.get("text", "")
        label = elem.get("label", "")

        # For tables without a text field, try to reconstruct from data
        if not text and label == "table":
            data = elem.get("data", {})
            grid = data.get("grid", [])
            if grid:
                rows = []
                for row in grid:
                    cells = [c.get("text", "") for c in row]
                    rows.append("| " + " | ".join(cells) + " |")
                text = "\n".join(rows)

        if text:
            # Add heading prefix based on label
            if label == "section_header":
                text = f"## {text}"
            elif label == "title":
                text = f"# {text}"
            pages.setdefault(page_no, []).append(text)

    return pages


def _get_page_markdown(
    session_factory: sessionmaker,
    artifacts_dir: Path,
    run_id: int,
    page_num: int,
) -> str | None:
    """Extract markdown for a single page from Docling JSON.

    ``page_num`` is 0-indexed (matching editor convention).
    Returns joined text for the page, or None if unavailable.
    """
    pages = _extract_page_texts_from_docling(
        session_factory, artifacts_dir, run_id
    )
    if not pages:
        return None

    # page_num is 0-indexed, Docling uses 1-indexed
    docling_page = page_num + 1
    texts = pages.get(docling_page, [])
    if not texts:
        return None

    return "\n\n".join(texts)


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def make_editor_router(
    *,
    config_manager: ConfigManager,
    session_factory: sessionmaker,
) -> APIRouter:
    """Create the editor API router.

    Endpoints:
        GET  /api/editor/page-info/{run_id}   — PDF metadata (page count, filename)
        POST /api/editor/render-page           — Render a PDF page to PNG
        POST /api/editor/vision-refine         — VLM-powered page correction
        GET  /api/editor/markdown/{run_id}     — Current markdown for a run
    """
    settings = Settings()
    artifacts_dir = Path(settings.atlas_artifacts_dir).resolve()

    r = APIRouter(
        prefix="/api/editor",
        tags=["editor"],
        dependencies=[Depends(require_admin_token)],
    )

    # ------------------------------------------------------------------
    # Resolve document → latest run
    # ------------------------------------------------------------------

    @r.get("/resolve-doc/{doc_id}", response_model=DocResolveResponse)
    async def resolve_doc(doc_id: str) -> DocResolveResponse:
        """Look up the latest workflow run for a document ID."""
        with session_factory() as session:
            run = get_latest_run_by_doc_id(session, doc_id=doc_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"No runs found for doc_id '{doc_id}'")
        meta = run.meta or {}
        return DocResolveResponse(
            doc_id=doc_id,
            run_id=run.id,
            doc_version=run.doc_version or "1",
            status=run.status or "unknown",
            source_filename=meta.get("source_filename", ""),
        )

    # ------------------------------------------------------------------
    # Page info
    # ------------------------------------------------------------------

    @r.get("/page-info/{run_id}", response_model=PageInfoResponse)
    async def get_page_info(run_id: int) -> PageInfoResponse:
        """Return page count and source metadata for a run's PDF."""
        pdf_bytes, filename = await run_in_threadpool(
            _find_source_pdf, session_factory, artifacts_dir, run_id
        )
        n_pages = await run_in_threadpool(page_count, pdf_bytes)
        # Get mime type from run meta
        with session_factory() as session:
            run = get_workflow_run(session, run_id=run_id)
            mime = (run.meta or {}).get("source_mime_type", "application/pdf") if run else "application/pdf"
        return PageInfoResponse(
            run_id=run_id,
            page_count=n_pages,
            source_filename=filename,
            source_mime_type=mime,
        )

    # ------------------------------------------------------------------
    # Render page → PNG
    # ------------------------------------------------------------------

    @r.post("/render-page")
    async def render_page_endpoint(req: PageRenderRequest) -> Response:
        """Render a single PDF page to PNG."""
        pdf_bytes, _ = await run_in_threadpool(
            _find_source_pdf, session_factory, artifacts_dir, req.run_id
        )
        crop = CropMargins(top=req.crop_top, bottom=req.crop_bottom)
        png_bytes = await run_in_threadpool(
            render_page, pdf_bytes, req.page_num, dpi=req.dpi, crop=crop
        )
        return Response(content=png_bytes, media_type="image/png")

    # ------------------------------------------------------------------
    # Source PDF passthrough (for PDF.js viewer)
    # ------------------------------------------------------------------

    @r.get("/source-pdf/{run_id}")
    async def get_source_pdf(run_id: int) -> Response:
        """Return the raw source PDF for a run (used by PDF.js viewer)."""
        pdf_bytes, filename = await run_in_threadpool(
            _find_source_pdf, session_factory, artifacts_dir, run_id
        )
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )

    # ------------------------------------------------------------------
    # Current markdown
    # ------------------------------------------------------------------

    @r.get("/markdown/{run_id}")
    async def get_markdown(run_id: int) -> dict[str, Any]:
        """Return the current markdown for a run."""
        md = await run_in_threadpool(
            _find_current_markdown, session_factory, artifacts_dir, run_id
        )
        if md is None:
            raise HTTPException(status_code=404, detail=f"No markdown found for run {run_id}")
        return {"run_id": run_id, "markdown": md}

    # ------------------------------------------------------------------
    # Per-page markdown (from Docling provenance)
    # ------------------------------------------------------------------

    @r.get("/page-markdown/{run_id}/{page_num}")
    async def get_page_markdown(run_id: int, page_num: int) -> dict[str, Any]:
        """Return the markdown content for a single PDF page.

        ``page_num`` is 0-indexed.  Extracts per-page text from the
        Docling JSON provenance data.  Returns ``{"markdown": "...",
        "page_num": N}`` or 404 if no Docling JSON is available.
        """
        md = await run_in_threadpool(
            _get_page_markdown, session_factory, artifacts_dir, run_id, page_num
        )
        if md is None:
            raise HTTPException(
                status_code=404,
                detail=f"No page-level markdown for run {run_id} page {page_num}. "
                       "Docling JSON may not be available.",
            )
        return {"run_id": run_id, "page_num": page_num, "markdown": md}

    # ------------------------------------------------------------------
    # VLM vision-refine
    # ------------------------------------------------------------------

    @r.post("/vision-refine", response_model=VisionRefineResponse)
    async def vision_refine(req: VisionRefineRequest) -> VisionRefineResponse:
        """Use a VLM to correct markdown for a specific PDF page.

        Renders the requested page to a high-res PNG, sends it alongside
        the current markdown to the vision model, and returns the corrected
        markdown.
        """
        # Resolve vision model from effective config (respects DB overrides)
        from atlas.config_versions import get_active_config_version
        yaml_defaults = config_manager.get()
        with session_factory() as session:
            active = get_active_config_version(session)
        if active is not None:
            models_cfg = active.payload.get("models", {}) or {}
        else:
            models_cfg = yaml_defaults.models
        registry = ModelRegistry(settings=settings, models_cfg=models_cfg)

        try:
            resolved = registry.resolve("vision_model")
        except KeyError:
            raise HTTPException(
                status_code=503,
                detail="No vision_model configured in models.yaml. "
                       "Add a vision_model role pointing to a multimodal model.",
            )

        provider = registry.provider_for(resolved.provider_name)

        # Get source PDF
        pdf_bytes, _ = await run_in_threadpool(
            _find_source_pdf, session_factory, artifacts_dir, req.run_id
        )

        # Render page to base64 data URI
        crop = CropMargins(top=req.crop_top, bottom=req.crop_bottom)
        page_uri = await run_in_threadpool(
            render_page_base64, pdf_bytes, req.page_num, dpi=req.dpi, crop=crop
        )

        # Build multimodal messages
        raw_messages = build_vision_messages(
            page_image_uri=page_uri,
            current_markdown=req.current_markdown,
            system_prompt=req.system_prompt,
        )

        # Convert to ChatMessage objects
        messages = [ChatMessage(role=m["role"], content=m["content"]) for m in raw_messages]

        # Call VLM
        log.info(
            "VLM vision-refine: run=%d page=%d model=%s dpi=%d",
            req.run_id, req.page_num, resolved.model_name, req.dpi,
        )

        corrected = await provider.chat(
            model=resolved.model_name,
            messages=messages,
            params=resolved.params,
        )

        return VisionRefineResponse(
            corrected_markdown=corrected,
            model=resolved.model_name,
            page_num=req.page_num,
        )

    # ------------------------------------------------------------------
    # Save markdown back to artifacts
    # ------------------------------------------------------------------

    @r.post("/save-markdown", response_model=SaveMarkdownResponse)
    async def save_markdown(req: SaveMarkdownRequest) -> SaveMarkdownResponse:
        """Overwrite the current markdown artifact for a run.

        Writes to ``runs/{run_id}/editor/markdown.md`` and records a
        new artifact ref with ``kind='editor_save'``.
        """
        import hashlib

        # Verify run exists
        with session_factory() as session:
            run = get_workflow_run(session, run_id=req.run_id)
            if run is None:
                raise HTTPException(status_code=404, detail=f"Run {req.run_id} not found")

        # Write the file
        save_dir = artifacts_dir / f"runs/{req.run_id}/editor"
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / "markdown.md"
        save_path.write_text(req.markdown, encoding="utf-8")

        rel_path = f"runs/{req.run_id}/editor/markdown.md"
        sha = hashlib.sha256(req.markdown.encode("utf-8")).hexdigest()

        # Record artifact ref
        from atlas.workflow_ledger import add_artifact_ref, ArtifactRefCreateRequest

        with session_factory() as session:
            add_artifact_ref(
                session,
                run_id=req.run_id,
                req=ArtifactRefCreateRequest(
                    kind="editor_save",
                    path=rel_path,
                    sha256=sha,
                    mime_type="text/markdown",
                    meta={"source": "editor", "chars": len(req.markdown)},
                ),
            )

        log.info("Editor save: run=%d path=%s chars=%d", req.run_id, rel_path, len(req.markdown))
        return SaveMarkdownResponse(
            run_id=req.run_id,
            path=rel_path,
            chars=len(req.markdown),
            message="Saved successfully",
        )

    # ------------------------------------------------------------------
    # LLM Refine (uses RefineNode from pipeline)
    # ------------------------------------------------------------------

    @r.post("/llm-refine", response_model=LlmRefineResponse)
    async def llm_refine(req: LlmRefineRequest) -> LlmRefineResponse:
        """Run the pipeline RefineNode on editor markdown content.

        Uses the configured ``refine_model`` role. Returns the refined
        markdown and a list of improvements detected.
        """
        from atlas.config_versions import get_active_config_version
        from atlas.pipeline.refine import RefineNode

        yaml_defaults = config_manager.get()
        with session_factory() as session:
            active = get_active_config_version(session)
        if active is not None:
            models_cfg = active.payload.get("models", {}) or {}
        else:
            models_cfg = yaml_defaults.models
        registry = ModelRegistry(settings=settings, models_cfg=models_cfg)

        try:
            resolved = registry.resolve("refine_model")
        except KeyError:
            raise HTTPException(
                status_code=503,
                detail="No refine_model configured in models.yaml.",
            )

        provider = registry.provider_for(resolved.provider_name)

        refine_node = RefineNode(
            provider=provider,
            model_name=resolved.model_name,
            model_params=resolved.params,
        )

        log.info(
            "Editor LLM refine: run=%d model=%s markdown_len=%d",
            req.run_id, resolved.model_name, len(req.markdown),
        )

        result = await refine_node.refine_document(
            markdown=req.markdown,
            judge_score=3,       # Assume needs improvement (editor-triggered)
            retry_count=0,
            max_retries=1,
        )

        return LlmRefineResponse(
            refined_markdown=result.refined_markdown,
            model=resolved.model_name,
            success=result.success,
            improvements=result.improvements_made,
        )

    # ------------------------------------------------------------------
    # Re-Judge (uses JudgeNode from pipeline)
    # ------------------------------------------------------------------

    @r.post("/re-judge", response_model=ReJudgeResponse)
    async def re_judge(req: ReJudgeRequest) -> ReJudgeResponse:
        """Run the pipeline JudgeNode on editor markdown content.

        Uses the configured ``judge_model`` role. Returns the quality
        score, per-dimension sub-scores, and rationale.
        """
        from atlas.config_versions import get_active_config_version
        from atlas.pipeline.judge import JudgeNode

        yaml_defaults = config_manager.get()
        with session_factory() as session:
            active = get_active_config_version(session)
        if active is not None:
            models_cfg = active.payload.get("models", {}) or {}
        else:
            models_cfg = yaml_defaults.models
        registry = ModelRegistry(settings=settings, models_cfg=models_cfg)

        try:
            resolved = registry.resolve("judge_model")
        except KeyError:
            raise HTTPException(
                status_code=503,
                detail="No judge_model configured in models.yaml.",
            )

        provider = registry.provider_for(resolved.provider_name)

        judge_node = JudgeNode(
            provider=provider,
            model_name=resolved.model_name,
            model_params=resolved.params,
        )

        log.info(
            "Editor re-judge: run=%d model=%s markdown_len=%d",
            req.run_id, resolved.model_name, len(req.markdown),
        )

        result = await judge_node.grade_document(
            markdown=req.markdown,
            judge_cutoff=req.judge_cutoff,
        )

        return ReJudgeResponse(
            score=result.score,
            sub_scores=result.sub_scores,
            rationale=result.confidence_rationale,
            needs_refinement=result.needs_refinement,
            model=resolved.model_name,
        )

    return r
