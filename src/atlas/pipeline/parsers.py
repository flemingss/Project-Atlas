"""Document parsing strategies for the ingest pipeline.

Implements the Strategy Pattern for PDF backend selection, replacing
the if/else branching in ``IngestNode.process_doc_bytes()``.

Each concrete ``DocumentParser`` encapsulates one parsing approach.
``FallbackParser`` composes multiple parsers with ordered fallback.
"""

from __future__ import annotations

import asyncio
import logging as _logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Any

from atlas.diagnostics import ErrorCode
from atlas.ingest.docling_adapter import DoclingIngestError, parse_document_path
from atlas.schemas import ParseProfile
from atlas.settings import Settings

if TYPE_CHECKING:
    # Import only for annotations: ingest.py imports this module at runtime.
    from atlas.pipeline.ingest import IngestResult

_logger = _logging.getLogger(__name__)


@dataclass
class ParserContext:
    """Shared configuration and services for document parsers."""

    diagnostics: Any
    settings: Settings
    pdf_cfg: dict[str, Any]


class DocumentParser(ABC):
    """Abstract base for document parsing strategies."""

    def __init__(self, ctx: ParserContext) -> None:
        self.ctx = ctx

    @abstractmethod
    async def parse(
        self,
        doc_bytes: bytes,
        source_mime_type: str,
        filename: str | None,
    ) -> "IngestResult | None":
        """Parse document bytes.

        Returns an ``IngestResult`` on success or handled failure,
        or ``None`` if this parser is unavailable / inapplicable.
        """
        ...


class DoclingParser(DocumentParser):
    """Parse via the Docling library (PDF/Office)."""

    async def parse(
        self,
        doc_bytes: bytes,
        source_mime_type: str,
        filename: str | None,
    ) -> "IngestResult":
        from atlas.pipeline.ingest import IngestResult

        try:
            if filename and "." in filename:
                suffix = "." + filename.rsplit(".", 1)[-1]
            else:
                known = {
                    "application/pdf": ".pdf",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
                    "application/msword": ".doc",
                    "application/vnd.ms-powerpoint": ".ppt",
                    "application/vnd.ms-excel": ".xls",
                }.get(source_mime_type)
                if known is None:
                    # Unknown mime with no filename: Docling detects format by
                    # extension, so only claim .pdf when the bytes agree —
                    # anything else gets a neutral suffix and fails detection
                    # cleanly instead of being parsed as a broken PDF.
                    known = ".pdf" if doc_bytes[:5] == b"%PDF-" else ".bin"
                suffix = known

            with NamedTemporaryFile(delete=True, suffix=suffix) as tmp:
                tmp.write(doc_bytes)
                tmp.flush()
                # Docling conversion is CPU-heavy and synchronous; run it off
                # the event loop so health checks and concurrent requests stay
                # responsive during a parse.
                parsed = await asyncio.to_thread(
                    parse_document_path,
                    doc_path=Path(tmp.name), source_mime_type=source_mime_type,
                )

            if not (parsed.markdown_projection or "").strip():
                self.ctx.diagnostics.log_warning(
                    component="ingest",
                    message="Docling produced an empty markdown projection (OCR/text extraction returned no content)",
                    context={
                        "source_mime_type": source_mime_type,
                        "filename": filename or "",
                    },
                )
                return IngestResult(
                    success=False,
                    markdown_projection="",
                    docling_json=parsed.docling_json,
                    parse_profile=parsed.parse_profile,
                    docling_schema_version=parsed.docling_schema_version,
                    error_code=ErrorCode.DOC_OCR_EMPTY,
                    error_message=(
                        "OCR/text extraction returned no content. The document may contain no selectable text "
                        "or the scan quality is too low for OCR."
                    ),
                    meta=parsed.meta,
                )
        except DoclingIngestError as e:
            return IngestResult(
                success=False,
                markdown_projection="",
                docling_json={},
                parse_profile=ParseProfile.PDF_TEXT,
                docling_schema_version="unknown",
                error_code=e.error_code,
                error_message=str(e),
            )
        except Exception as e:  # noqa: BLE001
            self.ctx.diagnostics.log_error(
                component="ingest",
                error_code=ErrorCode.DOC_PARSE_FAILED,
                message="PDF processing failed",
                context={"filename": filename or ""},
                exception=e,
            )
            return IngestResult(
                success=False,
                markdown_projection="",
                docling_json={},
                parse_profile=ParseProfile.PDF_TEXT,
                docling_schema_version="unknown",
                error_code=ErrorCode.DOC_PARSE_FAILED,
                error_message=str(e),
            )

        return IngestResult(
            success=True,
            markdown_projection=parsed.markdown_projection,
            docling_json=parsed.docling_json,
            parse_profile=parsed.parse_profile,
            docling_schema_version=parsed.docling_schema_version,
            meta={**(parsed.meta or {}), "extraction_backend": "docling"},
        )


class LayoutParser(DocumentParser):
    """Parse via the layout-aware PDF parser (ONNX models + OCR)."""

    async def parse(
        self,
        doc_bytes: bytes,
        source_mime_type: str,
        filename: str | None,
    ) -> "IngestResult | None":
        from atlas.pipeline.ingest import IngestResult

        try:
            from atlas.ingest.pdf_parser import LayoutPdfParser  # lazy import
        except Exception:
            _logger.debug("Layout parser import failed", exc_info=True)
            return None

        try:
            from atlas.ingest.model_manager import ModelManager

            mgr = ModelManager.get_instance(models_dir=self.ctx.settings.atlas_models_dir)
            if not all(mgr.models_available().values()):
                _logger.info("ONNX models not yet downloaded — downloading now …")
                await asyncio.to_thread(mgr.ensure_models)

            zoom = float(
                self.ctx.pdf_cfg.get("zoom", 0) or self.ctx.settings.atlas_layout_pdf_zoom,
            )
            parser = LayoutPdfParser(models_dir=self.ctx.settings.atlas_models_dir)
            # ONNX inference + OCR are synchronous; keep them off the event loop.
            result = await asyncio.to_thread(parser, doc_bytes, zoom=zoom)

            # Confidence gate
            min_conf = float(
                self.ctx.pdf_cfg.get("ocr_confidence_min", 0)
                or self.ctx.settings.atlas_layout_ocr_confidence_min,
            )
            if result.mean_ocr_confidence < min_conf:
                self.ctx.diagnostics.log_warning(
                    component="ingest",
                    message=f"Layout parser OCR confidence {result.mean_ocr_confidence:.2f} < {min_conf}",
                    context={"filename": filename or ""},
                )
                return None  # falls back to next parser in chain

            markdown = result.markdown
            meta: dict[str, Any] = {
                "extraction_backend": "layout",
                "mean_ocr_confidence": result.mean_ocr_confidence,
                "layout_confidence": result.layout_confidence,
                "ocr_coverage": result.ocr_coverage,
                "estimated_is_scanned": result.estimated_is_scanned,
                "page_count": result.page_count,
            }
            docling_json = {
                "content": markdown,
                "mime_type": "application/pdf",
                "parsed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "parser": "layout",
                "metadata": result.metadata,
            }

            return IngestResult(
                success=True,
                markdown_projection=markdown,
                docling_json=docling_json,
                parse_profile=ParseProfile.PDF_LAYOUT,
                docling_schema_version="1.0",
                meta=meta,
            )
        except Exception as exc:
            self.ctx.diagnostics.log_warning(
                component="ingest",
                message=f"Layout parser failed: {exc}",
                context={"filename": filename or ""},
            )
            _logger.debug("Layout parser traceback", exc_info=True)
            return None


class VisionParser(DocumentParser):
    """Parse PDF via VLM — render each page and send to vision model."""

    async def parse(
        self,
        doc_bytes: bytes,
        source_mime_type: str,
        filename: str | None,
    ) -> "IngestResult":
        from atlas.pipeline.ingest import IngestResult
        from atlas.ingest.page_renderer import (
            CropMargins,
            build_vision_messages,
            page_count as pdf_page_count,
            render_page_base64,
        )
        from atlas.llm.provider import ChatMessage
        from atlas.llm.registry import ModelRegistry
        from atlas.vlm_ingest.stitcher import PageResult, stitch_pages

        try:
            n_pages = pdf_page_count(doc_bytes)
        except Exception as exc:
            return IngestResult(
                success=False,
                markdown_projection="",
                docling_json={},
                parse_profile=ParseProfile.PDF_LAYOUT,
                docling_schema_version="unknown",
                error_code=ErrorCode.DOC_PARSE_FAILED,
                error_message=f"Failed to read PDF pages: {exc}",
            )

        # Resolve vision model
        try:
            from atlas.config_manager import ConfigManager

            config_dir = Path(self.ctx.settings.atlas_config_dir).resolve()
            config_manager = ConfigManager(
                config_dir=config_dir,
                profile=self.ctx.settings.atlas_llm_profile or None,
            )
            models_cfg = config_manager.get().models
            model_registry = ModelRegistry(
                settings=self.ctx.settings, models_cfg=models_cfg,
            )
            resolved = model_registry.resolve("vision_model")
            provider = model_registry.provider_for(resolved.provider_name)
        except KeyError:
            return IngestResult(
                success=False,
                markdown_projection="",
                docling_json={},
                parse_profile=ParseProfile.PDF_LAYOUT,
                docling_schema_version="unknown",
                error_code=ErrorCode.DOC_PARSE_FAILED,
                error_message="No vision_model configured in models.yaml for backend=vision.",
            )

        # Get crop settings from pipeline config
        vlm_cfg = self.ctx.pdf_cfg.get("vlm", {}) or {}
        dpi = int(vlm_cfg.get("dpi", 200))
        crop = CropMargins(
            top=float(vlm_cfg.get("crop_top", 0.04)),
            bottom=float(vlm_cfg.get("crop_bottom", 0.04)),
        )
        system_prompt = vlm_cfg.get("system_prompt")

        # Process pages sequentially
        page_results: list[PageResult] = []
        for p in range(n_pages):
            try:
                # PyMuPDF rendering is synchronous; keep it off the event loop.
                page_uri = await asyncio.to_thread(
                    render_page_base64, doc_bytes, p, dpi=dpi, crop=crop
                )
                raw_messages = build_vision_messages(
                    page_image_uri=page_uri,
                    current_markdown="",
                    system_prompt=system_prompt,
                )
                messages = [
                    ChatMessage(role=m["role"], content=m["content"])
                    for m in raw_messages
                ]

                _logger.info(
                    "VLM ingest (headless): page %d/%d model=%s dpi=%d file=%s",
                    p + 1, n_pages, resolved.model_name, dpi, filename or "unnamed",
                )

                corrected = await provider.chat(
                    model=resolved.model_name,
                    messages=messages,
                    params=resolved.params,
                )

                page_results.append(PageResult(
                    page_num=p,
                    markdown=corrected,
                    model=resolved.model_name,
                    dpi=dpi,
                    crop_top=crop.top,
                    crop_bottom=crop.bottom,
                ))
            except Exception as exc:
                _logger.warning("VLM page %d failed: %s — skipping", p, exc)

        if not page_results:
            return IngestResult(
                success=False,
                markdown_projection="",
                docling_json={},
                parse_profile=ParseProfile.PDF_LAYOUT,
                docling_schema_version="unknown",
                error_code=ErrorCode.DOC_PARSE_FAILED,
                error_message="VLM failed on all pages.",
            )

        # Stitch deterministically
        stitched = stitch_pages(page_results)

        meta: dict[str, Any] = {
            "extraction_backend": "vision",
            "vlm_model": resolved.model_name,
            "dpi": dpi,
            "page_count": n_pages,
            "pages_processed": stitched.pages_processed,
            "tables_merged": stitched.tables_merged,
            "headings_merged": stitched.headings_merged,
        }
        docling_json = {
            "content": stitched.markdown,
            "mime_type": "application/pdf",
            "parsed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "parser": "vision",
            "metadata": meta,
        }

        return IngestResult(
            success=True,
            markdown_projection=stitched.markdown,
            docling_json=docling_json,
            parse_profile=ParseProfile.PDF_LAYOUT,
            docling_schema_version="1.0",
            meta=meta,
        )


class FallbackParser(DocumentParser):
    """Try parsers in order, returning the first successful result.

    If all parsers fail, returns the result from the *first* parser
    (which typically has the most informative error).
    """

    def __init__(self, ctx: ParserContext, parsers: list[DocumentParser]) -> None:
        super().__init__(ctx)
        self._parsers = parsers

    async def parse(
        self,
        doc_bytes: bytes,
        source_mime_type: str,
        filename: str | None,
    ) -> "IngestResult | None":
        first_result = None
        for parser in self._parsers:
            result = await parser.parse(doc_bytes, source_mime_type, filename)
            if first_result is None:
                first_result = result
            if result is not None and result.success:
                return result
            parser_name = type(parser).__name__
            _logger.info(
                "%s failed/unavailable for %s — trying next",
                parser_name, filename or "unnamed",
            )
        return first_result


def build_parser(backend: str, ctx: ParserContext) -> DocumentParser:
    """Build the appropriate parser strategy for the given backend name."""
    docling = DoclingParser(ctx)
    layout = LayoutParser(ctx)
    vision = VisionParser(ctx)

    strategies: dict[str, DocumentParser] = {
        "auto": FallbackParser(ctx, [docling, layout]),
        "auto_layout": FallbackParser(ctx, [layout, docling]),
        "vision": vision,
        "layout": layout,
        "docling": docling,
    }

    return strategies.get(backend, docling)
