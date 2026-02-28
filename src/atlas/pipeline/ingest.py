"""Ingest node for Project Atlas pipeline (HLD section 2: Ingest).

Handles document ingestion and conversion to DoclingDocument format.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import re

from atlas.diagnostics import ErrorCode, get_diagnostics
from atlas.ingest.docling_adapter import DoclingIngestError, parse_document_path, parse_html_string
from atlas.schemas import ParseProfile
from atlas.settings import Settings

import logging as _logging

_logger = _logging.getLogger(__name__)


@dataclass
class IngestResult:
    """Result from document ingestion."""

    success: bool
    markdown_projection: str
    docling_json: dict[str, Any]
    parse_profile: ParseProfile
    docling_schema_version: str
    error_code: ErrorCode | None = None
    error_message: str | None = None
    meta: dict[str, Any] | None = None


class IngestNode:
    """Ingest node: Convert documents to DoclingDocument format (HLD section 2).

    Actions:
    - Convert PDF/Office to DoclingDocument JSON
    - Store full JSON as structural source (ground truth)
    - Generate Markdown projection for LLM consumption
    - Track source_mime_type and parse_profile
    - Version tracking with docling_schema_version
    """

    def __init__(self):
        self.diagnostics = get_diagnostics()
        self.settings = Settings()

    async def process_text(self, *, text: str, mime_type: str) -> IngestResult:
        """Process plain text input.

        For MVP, plain text is already in consumable format.
        """
        self.diagnostics.log_info(
            component="ingest",
            message=f"Processing text input ({len(text)} chars)",
            context={"mime_type": mime_type},
        )

        docling_json = {
            "content": text,
            "mime_type": mime_type,
            "parsed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

        return IngestResult(
            success=True,
            markdown_projection=text,
            docling_json=docling_json,
            parse_profile=ParseProfile.TEXT,
            docling_schema_version="1.0",
        )

    async def process_doc_bytes(
        self,
        *,
        doc_bytes: bytes,
        source_mime_type: str,
        filename: str | None = None,
    ) -> IngestResult:
        """Process document bytes via layout parser or Docling (PDF/Office).

        For PDFs, the backend is selected by ``atlas_pdf_parser_backend``:
        - ``auto`` (default): try layout parser first, fall back to Docling.
        - ``layout``: layout parser only.
        - ``docling``: Docling only (legacy behaviour).
        """
        backend = self.settings.atlas_pdf_parser_backend.lower()
        is_pdf = source_mime_type == "application/pdf"

        # ------ Layout parser path (PDFs only) --------------------------------
        if is_pdf and backend in ("auto", "layout"):
            result = self._try_layout_parser(doc_bytes, filename)
            if result is not None:
                # Run quality gates on the layout result
                return self._apply_pdf_quality_gates(result, source_mime_type, filename)

            if backend == "layout":
                # Layout was forced but failed — don't fall through
                return IngestResult(
                    success=False,
                    markdown_projection="",
                    docling_json={},
                    parse_profile=ParseProfile.PDF_LAYOUT,
                    docling_schema_version="unknown",
                    error_code=ErrorCode.DOC_PARSE_FAILED,
                    error_message="Layout parser failed and backend=layout (no fallback).",
                )
            _logger.info("Layout parser unavailable/failed for %s — falling back to Docling", filename or "unnamed")

        # ------ Docling path (all doc types) ----------------------------------
        return await self._docling_parse(doc_bytes, source_mime_type, filename)

    # ------------------------------------------------------------------
    # Layout parser helper
    # ------------------------------------------------------------------

    def _try_layout_parser(
        self, doc_bytes: bytes, filename: str | None,
    ) -> IngestResult | None:
        """Attempt layout-aware PDF parsing. Returns *None* on failure."""
        try:
            from atlas.ingest.pdf_parser import LayoutPdfParser  # lazy import
        except Exception:
            _logger.debug("Layout parser import failed", exc_info=True)
            return None

        try:
            from atlas.ingest.model_manager import ModelManager
            mgr = ModelManager.get_instance(models_dir=self.settings.atlas_models_dir)
            if not all(mgr.models_available().values()):
                _logger.info("ONNX models not yet downloaded — downloading now …")
                mgr.ensure_models()

            zoom = self.settings.atlas_layout_pdf_zoom
            parser = LayoutPdfParser(models_dir=self.settings.atlas_models_dir)
            result = parser(doc_bytes, zoom=zoom)

            # Confidence gate
            min_conf = self.settings.atlas_layout_ocr_confidence_min
            if result.mean_ocr_confidence < min_conf:
                self.diagnostics.log_warning(
                    component="ingest",
                    message=f"Layout parser OCR confidence {result.mean_ocr_confidence:.2f} < {min_conf}",
                    context={"filename": filename or ""},
                )
                return None  # falls back to Docling if backend=auto

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
            self.diagnostics.log_warning(
                component="ingest",
                message=f"Layout parser failed: {exc}",
                context={"filename": filename or ""},
            )
            _logger.debug("Layout parser traceback", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Docling path (original logic)
    # ------------------------------------------------------------------

    async def _docling_parse(
        self,
        doc_bytes: bytes,
        source_mime_type: str,
        filename: str | None,
    ) -> IngestResult:
        """Parse via Docling (PDF/Office) — original ingest path."""
        try:
            suffix = ".pdf"
            if filename and "." in filename:
                suffix = "." + filename.rsplit(".", 1)[-1]
            else:
                suffix = {
                    "application/pdf": ".pdf",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
                    "application/msword": ".doc",
                    "application/vnd.ms-powerpoint": ".ppt",
                    "application/vnd.ms-excel": ".xls",
                }.get(source_mime_type, ".pdf")

            with NamedTemporaryFile(delete=True, suffix=suffix) as tmp:
                tmp.write(doc_bytes)
                tmp.flush()
                parsed = parse_document_path(doc_path=Path(tmp.name), source_mime_type=source_mime_type)

            if not (parsed.markdown_projection or "").strip():
                self.diagnostics.log_warning(
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
            self.diagnostics.log_error(
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

        # Quality gates for PDF outputs to avoid indexing junk.
        result = IngestResult(
            success=True,
            markdown_projection=parsed.markdown_projection,
            docling_json=parsed.docling_json,
            parse_profile=parsed.parse_profile,
            docling_schema_version=parsed.docling_schema_version,
            meta={**(parsed.meta or {}), "extraction_backend": "docling"},
        )
        if source_mime_type == "application/pdf":
            return self._apply_pdf_quality_gates(result, source_mime_type, filename)
        return result

    # ------------------------------------------------------------------
    # Shared quality gates
    # ------------------------------------------------------------------

    def _apply_pdf_quality_gates(
        self, result: IngestResult, source_mime_type: str, filename: str | None,
    ) -> IngestResult:
        """Apply PDF quality gates to an IngestResult. Returns original or failure."""
        qm = _pdf_quality_metrics(text=result.markdown_projection)
        enforce_len = bool(qm.get("chars", 0) >= 100)

        min_chars = int(self.settings.atlas_pdf_quality_min_chars)
        min_words = int(self.settings.atlas_pdf_quality_min_words)
        alpha_min = float(self.settings.atlas_pdf_quality_alpha_ratio_min)
        garbled_max = float(self.settings.atlas_pdf_quality_garbled_ratio_max)

        too_garbled = bool(float(qm.get("garbled_ratio") or 0.0) > garbled_max)
        mostly_symbols = bool(float(qm.get("alpha_ratio") or 0.0) < alpha_min and enforce_len)
        too_short = bool(int(qm.get("chars") or 0) < min_chars or int(qm.get("words") or 0) < min_words)

        if too_garbled or mostly_symbols or too_short:
            self.diagnostics.log_warning(
                component="ingest",
                message="PDF extraction failed quality gates",
                context={
                    "quality": qm,
                    "min_chars": min_chars,
                    "min_words": min_words,
                    "alpha_min": alpha_min,
                    "garbled_max": garbled_max,
                    "enforce_len": enforce_len,
                },
            )
            return IngestResult(
                success=False,
                markdown_projection="",
                docling_json=result.docling_json,
                parse_profile=result.parse_profile,
                docling_schema_version=result.docling_schema_version,
                error_code=ErrorCode.DOC_EXTRACT_LOW_QUALITY,
                error_message=f"PDF extraction produced low-quality text (quality={qm})",
                meta={**(result.meta or {}), "quality": qm},
            )

        # Attach quality metrics to meta
        result.meta = {**(result.meta or {}), "quality": qm}
        return result

    async def process_document(self, *, content: str | bytes, mime_type: str) -> IngestResult:
        """Process a document based on its MIME type.

        Routes to appropriate parser based on content type.
        """
        with self.diagnostics.trace_operation("ingest_document", {"mime_type": mime_type}):
            if mime_type == "text/plain" and isinstance(content, str):
                return await self.process_text(text=content, mime_type=mime_type)

            if isinstance(content, (bytes, bytearray)) and mime_type in {"text/plain", "text/markdown"}:
                try:
                    text = bytes(content).decode("utf-8")
                except Exception:  # noqa: BLE001
                    text = bytes(content).decode("utf-8", errors="replace")

                parsed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                docling_json = {"content": text, "mime_type": mime_type, "parsed_at": parsed_at}
                return IngestResult(
                    success=True,
                    markdown_projection=text,
                    docling_json=docling_json,
                    parse_profile=ParseProfile.MARKDOWN if mime_type == "text/markdown" else ParseProfile.TEXT,
                    docling_schema_version="1.0",
                )

            if isinstance(content, (bytes, bytearray)) and mime_type == "text/html":
                try:
                    html = bytes(content).decode("utf-8")
                except Exception:  # noqa: BLE001
                    html = bytes(content).decode("utf-8", errors="replace")

                markdown: str
                docling_json: dict[str, Any]
                try:
                    parsed = parse_html_string(html=html, name=None)
                    markdown = parsed.markdown_projection
                    docling_json = parsed.docling_json
                    profile = parsed.parse_profile
                    schema_ver = parsed.docling_schema_version
                except Exception:
                    markdown = ""
                    try:
                        from markdownify import markdownify as _markdownify  # type: ignore[import-not-found]

                        markdown = _markdownify(html)
                    except Exception:
                        markdown = re.sub(r"<[^>]+>", " ", html)
                        markdown = re.sub(r"\s+", " ", markdown).strip() + "\n"

                    parsed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                    docling_json = {"content": html, "mime_type": mime_type, "parsed_at": parsed_at}
                    profile = ParseProfile.TEXT
                    schema_ver = "1.0"

                return IngestResult(
                    success=True,
                    markdown_projection=markdown,
                    docling_json=docling_json,
                    parse_profile=profile,
                    docling_schema_version=schema_ver,
                )

            if isinstance(content, (bytes, bytearray)) and mime_type in {
                "application/pdf",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/msword",
                "application/vnd.ms-powerpoint",
                "application/vnd.ms-excel",
            }:
                return await self.process_doc_bytes(
                    doc_bytes=bytes(content),
                    source_mime_type=mime_type,
                    filename=None,
                )

            self.diagnostics.log_error(
                component="ingest",
                error_code=ErrorCode.INVALID_MIME_TYPE,
                message=f"Unsupported MIME type: {mime_type}",
            )
            return IngestResult(
                success=False,
                markdown_projection="",
                docling_json={},
                parse_profile=ParseProfile.TEXT,
                docling_schema_version="1.0",
                error_code=ErrorCode.INVALID_MIME_TYPE,
                error_message=f"Unsupported MIME type: {mime_type}",
            )


def _pdf_quality_metrics(*, text: str) -> dict[str, Any]:
    t = (text or "").strip()
    if not t:
        return {"chars": 0, "words": 0, "alpha_ratio": 0.0, "garbled_ratio": 0.0}

    chars = len(t)
    words = len(re.findall(r"\b\w+\b", t))
    letters = sum(1 for c in t if c.isalpha())
    non_space = sum(1 for c in t if not c.isspace())
    alpha_ratio = (letters / non_space) if non_space else 0.0

    # "Garbled" heuristic: replacement char + control chars (excluding common whitespace).
    repl = t.count("\ufffd") + t.count("?") * 0  # keep replacement explicit; don't treat '?' as garble
    ctrl = sum(1 for c in t if ord(c) < 32 and c not in "\n\t\r")
    garbled_ratio = ((repl + ctrl) / chars) if chars else 0.0

    return {
        "chars": chars,
        "words": words,
        "alpha_ratio": float(alpha_ratio),
        "garbled_ratio": float(garbled_ratio),
    }
