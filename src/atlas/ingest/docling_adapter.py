from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atlas.diagnostics import ErrorCode, get_diagnostics
from atlas.schemas import ParseProfile


class DoclingUnavailableError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Docling is not installed. Install optional dependencies with: pip install -e .[docling]"
        )


class DoclingParseError(RuntimeError):
    pass


@dataclass(frozen=True)
class DoclingParseResult:
    markdown_projection: str
    docling_json: dict[str, Any]
    parse_profile: ParseProfile
    docling_schema_version: str
    meta: dict[str, Any]


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


def parse_pdf_path(*, pdf_path: Path) -> DoclingParseResult:
    """Parse a PDF using Docling.

    Deprecated wrapper around `parse_document_path`.

    Raises:
      - DoclingUnavailableError if Docling isn't installed
      - DoclingParseError for parsing/export failures
    """
    return parse_document_path(doc_path=pdf_path, source_mime_type="application/pdf")


def parse_document_path(*, doc_path: Path, source_mime_type: str) -> DoclingParseResult:
    """Parse a document (PDF/Office) using Docling.

    Raises:
      - DoclingUnavailableError if Docling isn't installed
      - DoclingParseError for parsing/export failures
    """
    diagnostics = get_diagnostics()

    try:
        from docling.document_converter import DocumentConverter  # type: ignore[import-not-found]
    except Exception as e:  # noqa: BLE001
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
        conversion = converter.convert(str(doc_path))
    except Exception as e:  # noqa: BLE001
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
        meta={"converter": "docling", "source_mime_type": source_mime_type},
    )
