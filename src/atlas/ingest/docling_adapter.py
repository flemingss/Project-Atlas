from __future__ import annotations

import concurrent.futures
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atlas.diagnostics import ErrorCode, get_diagnostics
from atlas.retry import RetryConfig, get_retry_config, sync_retry
from atlas.schemas import ParseProfile
from atlas.settings import Settings

log = logging.getLogger(__name__)


class DoclingIngestError(RuntimeError):
    def __init__(self, *, error_code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class DoclingUnavailableError(DoclingIngestError):
    def __init__(self) -> None:
        super().__init__(
            error_code=ErrorCode.DOC_PARSE_DEPENDENCY_MISSING,
            message="Docling is not installed. Install dependencies with: pip install -e .",
        )


class DoclingParseError(DoclingIngestError):
    def __init__(self, message: str) -> None:
        super().__init__(error_code=ErrorCode.DOC_PARSE_FAILED, message=message)


class DoclingTimeoutError(DoclingIngestError):
    def __init__(self, *, timeout_s: float) -> None:
        super().__init__(
            error_code=ErrorCode.DOC_PARSE_TIMEOUT,
            message=f"Docling conversion timed out after {timeout_s:.1f}s",
        )


class DoclingLimitsError(DoclingIngestError):
    pass


@dataclass(frozen=True)
class DoclingParseResult:
    markdown_projection: str
    docling_json: dict[str, Any]
    parse_profile: ParseProfile
    docling_schema_version: str
    meta: dict[str, Any]


def _pdf_preflight(*, pdf_path: Path) -> dict[str, Any]:
    """Fast PDF preflight for diagnostics and policy decisions.

    Best-effort: if PyMuPDF isn't available, returns an empty dict.
    """
    diagnostics = get_diagnostics()
    try:
        import fitz  # PyMuPDF
    except Exception as e:
        diagnostics.log_warning(
            component="ingest",
            message="PyMuPDF not available; skipping PDF preflight",
            context={"dependency": "PyMuPDF", "exception": str(e)},
        )
        return {}

    doc = None
    try:
        doc = fitz.open(str(pdf_path))
        pages = int(doc.page_count)
        rotations: list[int] = []
        text_chars_total = 0
        image_count_total = 0
        drawing_count_total = 0

        # Keep this cheap; avoid heavy render.
        max_scan = min(pages, 25)  # sample first N pages for speed
        for i in range(max_scan):
            page = doc.load_page(i)
            rotations.append(int(getattr(page, "rotation", 0) or 0))
            try:
                txt = page.get_text("text") or ""
            except Exception:
                txt = ""
            text_chars_total += len(txt.strip())

            try:
                image_count_total += len(page.get_images(full=True) or [])
            except Exception:
                pass

            try:
                drawing_count_total += len(page.get_drawings() or [])
            except Exception:
                pass

        rot_set = sorted(set(rotations))
        has_rotation = any(r % 360 != 0 for r in rot_set)
        mixed_rotation = len({r % 360 for r in rot_set}) > 1

        # Heuristic: little/no extractable text but rich graphical content.
        text_as_shapes_suspected = bool(text_chars_total < 20 and (image_count_total + drawing_count_total) > 10)

        return {
            "pages": pages,
            "sampled_pages": max_scan,
            "rotations": rot_set,
            "has_rotation": has_rotation,
            "mixed_rotation": mixed_rotation,
            "sample_text_chars": text_chars_total,
            "sample_images": image_count_total,
            "sample_drawings": drawing_count_total,
            "text_as_shapes_suspected": text_as_shapes_suspected,
        }
    finally:
        try:
            if doc is not None:
                doc.close()
        except Exception:
            pass


def _docling_convert(*, converter: Any, source: str, timeout_s: float) -> Any:
    """Run Docling conversion with a timeout and retry.

    Docling conversion can be CPU-heavy; run in a thread so we can time out.
    Retries on DoclingTimeoutError using the ``docling`` retry config.
    """

    def _single_attempt() -> Any:
        # NOTE: Do not use ThreadPoolExecutor as a context manager here.
        # If we raise on timeout inside a `with` block, the executor's __exit__ will
        # call shutdown(wait=True) and block until the stuck conversion finishes,
        # defeating the timeout and making the API appear to hang/crash.
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(converter.convert, source)
        try:
            return fut.result(timeout=float(timeout_s))
        except concurrent.futures.TimeoutError as e:
            fut.cancel()
            raise DoclingTimeoutError(timeout_s=float(timeout_s)) from e
        finally:
            # Best-effort: don't wait for a stuck conversion.
            ex.shutdown(wait=False, cancel_futures=True)

    cfg = get_retry_config("docling")
    retry_cfg = RetryConfig(
        max_retries=cfg.max_retries,
        base_delay_s=cfg.base_delay_s,
        max_delay_s=cfg.max_delay_s,
        jitter=cfg.jitter,
        retryable_exceptions=(DoclingTimeoutError,),
    )
    return sync_retry(
        _single_attempt,
        config=retry_cfg,
        subsystem="docling",
        operation="convert",
    )


def _try_export_docling_json(doc: Any) -> dict[str, Any]:
    """Best-effort conversion of a Docling document object to a JSON-serializable dict."""
    # Common patterns across docling versions.
    for attr in ("export_to_dict", "to_dict"):
        fn = getattr(doc, attr, None)
        if callable(fn):
            out = fn()
            if isinstance(out, dict):
                return out

    for attr in ("model_dump",):
        fn = getattr(doc, attr, None)
        if callable(fn):
            out = fn()
            if isinstance(out, dict):
                return out

    # Fallback: try to JSON round-trip (may still fail).
    try:
        return json.loads(json.dumps(doc, default=str))
    except Exception:
        return {"docling_document": str(doc)}


def _try_export_markdown(doc: Any) -> str | None:
    for attr in (
        "export_to_markdown",
        "to_markdown",
        "export_to_md",
    ):
        fn = getattr(doc, attr, None)
        if callable(fn):
            out = fn()
            if isinstance(out, str):
                return out
    return None


def parse_document_path(*, doc_path: Path, source_mime_type: str) -> DoclingParseResult:
    """Parse a document (PDF/Office) using Docling.

    Raises:
      - DoclingUnavailableError if Docling isn't installed
      - DoclingParseError for parsing/export failures
    """
    diagnostics = get_diagnostics()
    settings = Settings()

    try:
        from docling.document_converter import DocumentConverter  # type: ignore[import-not-found]
    except Exception as e:
        diagnostics.log_error(
            component="ingest",
            error_code=ErrorCode.DOC_PARSE_DEPENDENCY_MISSING,
            message="Docling dependency is not available",
            context={"dependency": "docling"},
            exception=e,
        )
        raise DoclingUnavailableError() from e

    preflight: dict[str, Any] = {}
    if source_mime_type == "application/pdf":
        try:
            preflight = _pdf_preflight(pdf_path=doc_path)
        except Exception as e:
            diagnostics.log_warning(
                component="ingest",
                message="PDF preflight failed; continuing without it",
                context={"doc_path": str(doc_path), "exception": str(e)},
            )
            preflight = {}

        try:
            size_bytes = int(doc_path.stat().st_size)
        except Exception:
            size_bytes = 0

        if size_bytes and size_bytes > int(settings.atlas_pdf_max_bytes):
            raise DoclingLimitsError(
                error_code=ErrorCode.DOC_SIZE_LIMIT_EXCEEDED,
                message=f"PDF exceeds size limit ({size_bytes} bytes > {int(settings.atlas_pdf_max_bytes)} bytes)",
            )
        pages = int(preflight.get("pages") or 0)
        if pages and pages > int(settings.atlas_pdf_max_pages):
            raise DoclingLimitsError(
                error_code=ErrorCode.DOC_PAGE_LIMIT_EXCEEDED,
                message=f"PDF exceeds page limit ({pages} pages > {int(settings.atlas_pdf_max_pages)} pages)",
            )

    try:
        conversion = None
        method = "docling_default"

        # PDFs: first try extracting embedded text without OCR.
        # This avoids OCR-detection quirks for PDFs that have selectable text.
        if source_mime_type == "application/pdf":
            try:
                from docling.datamodel.base_models import (
                    InputFormat,  # type: ignore[import-not-found]
                )
                from docling.datamodel.pipeline_options import (
                    PdfPipelineOptions,  # type: ignore[import-not-found]
                )
                from docling.document_converter import (
                    PdfFormatOption,  # type: ignore[import-not-found]
                )

                prefer_embedded = bool((preflight.get("sample_text_chars") or 0) >= 20)
                prefer_ocr = bool(preflight.get("text_as_shapes_suspected"))

                pdf_text_only = PdfPipelineOptions()
                pdf_text_only.do_ocr = False

                converter = DocumentConverter(
                    format_options={
                        InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_text_only),
                    }
                )
                if prefer_ocr and not prefer_embedded:
                    converter = DocumentConverter()
                    conversion = _docling_convert(
                        converter=converter,
                        source=str(doc_path),
                        timeout_s=float(settings.atlas_docling_timeout_s),
                    )
                    method = "ocr"
                else:
                    conversion = _docling_convert(
                        converter=converter,
                        source=str(doc_path),
                        timeout_s=float(settings.atlas_docling_timeout_s),
                    )
                    method = "embedded_text"

                doc = getattr(conversion, "document", None)
                if doc is not None:
                    md = _try_export_markdown(doc) or ""
                    if not md.strip():
                        # Fall back to Docling defaults (usually OCR auto) when the embedded-text pass is empty.
                        converter = DocumentConverter()
                        conversion = _docling_convert(
                            converter=converter,
                            source=str(doc_path),
                            timeout_s=float(settings.atlas_docling_timeout_s),
                        )
                        method = "ocr"
            except DoclingIngestError:
                raise
            except Exception:
                # Any docling API drift or option mismatch shouldn't break ingestion; fall back to defaults.
                converter = DocumentConverter()
                conversion = _docling_convert(
                    converter=converter,
                    source=str(doc_path),
                    timeout_s=float(settings.atlas_docling_timeout_s),
                )
                method = "docling_default"
        else:
            converter = DocumentConverter()
            conversion = _docling_convert(
                converter=converter,
                source=str(doc_path),
                timeout_s=float(settings.atlas_docling_timeout_s),
            )

    except DoclingIngestError:
        raise
    except Exception as e:
        diagnostics.log_error(
            component="ingest",
            error_code=ErrorCode.DOC_PARSE_FAILED,
            message="Docling conversion failed",
            context={"doc_path": str(doc_path), "source_mime_type": source_mime_type},
            exception=e,
        )
        raise DoclingParseError(str(e)) from e

    doc = getattr(conversion, "document", None)
    if doc is None:
        raise DoclingParseError("Docling conversion returned no document")

    docling_json = _try_export_docling_json(doc)
    markdown = _try_export_markdown(doc)
    if markdown is None:
        # Actionable, but still return something usable.
        diagnostics.log_warning(
            component="ingest",
            message="Docling document has no markdown exporter; falling back to JSON string",
            context={"source_mime_type": source_mime_type},
        )
        markdown = json.dumps(docling_json, sort_keys=True, ensure_ascii=False)

    profile = ParseProfile.PDF_TEXT if source_mime_type == "application/pdf" else ParseProfile.TEXT

    return DoclingParseResult(
        markdown_projection=markdown,
        docling_json=docling_json,
        parse_profile=profile,
        docling_schema_version=str(docling_json.get("schema_version", "unknown")),
        meta={
            "converter": "docling",
            "source_mime_type": source_mime_type,
            "pdf_preflight": preflight,
            "extraction_method": method if source_mime_type == "application/pdf" else "docling_default",
        },
    )


def parse_html_string(*, html: str, name: str | None = None) -> DoclingParseResult:
    """Parse an HTML string using Docling.

    This uses Docling's `convert_string` path (HTML only). It intentionally raises
    DoclingUnavailableError if Docling isn't installed.
    """
    diagnostics = get_diagnostics()

    try:
        from docling.datamodel.base_models import InputFormat  # type: ignore[import-not-found]
        from docling.document_converter import DocumentConverter  # type: ignore[import-not-found]
    except Exception as e:
        diagnostics.log_error(
            component="ingest",
            error_code=ErrorCode.DOC_PARSE_DEPENDENCY_MISSING,
            message="Docling dependency is not available",
            context={"dependency": "docling"},
            exception=e,
        )
        raise DoclingUnavailableError() from e

    try:
        converter = DocumentConverter()
        conversion = converter.convert_string(html, format=InputFormat.HTML, name=name)
    except Exception as e:
        diagnostics.log_error(
            component="ingest",
            error_code=ErrorCode.DOC_PARSE_FAILED,
            message="Docling HTML conversion failed",
            context={"name": name or ""},
            exception=e,
        )
        raise DoclingParseError(str(e)) from e

    doc = getattr(conversion, "document", None)
    if doc is None:
        raise DoclingParseError("Docling conversion returned no document")

    docling_json = _try_export_docling_json(doc)
    markdown = _try_export_markdown(doc)
    if markdown is None:
        markdown = json.dumps(docling_json, sort_keys=True, ensure_ascii=False)

    return DoclingParseResult(
        markdown_projection=markdown,
        docling_json=docling_json,
        parse_profile=ParseProfile.TEXT,
        docling_schema_version=str(docling_json.get("schema_version", "unknown")),
        meta={"converter": "docling", "source_mime_type": "text/html"},
    )
