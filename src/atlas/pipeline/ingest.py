"""Ingest node for Project Atlas pipeline (HLD section 2: Ingest).

Handles document ingestion and conversion to DoclingDocument format.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from atlas.diagnostics import ErrorCode, get_diagnostics
from atlas.ingest.docling_adapter import (
    DoclingParseError,
    DoclingUnavailableError,
    parse_document_path,
)
from atlas.schemas import ParseProfile


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

    async def process_text(self, *, text: str, mime_type: str) -> IngestResult:
        """Process plain text input.

        For MVP, plain text is already in consumable format.
        Full implementation would use Docling for richer document types.
        """
        self.diagnostics.log_info(
            component="ingest",
            message=f"Processing text input ({len(text)} chars)",
            context={"mime_type": mime_type},
        )

        # For text/plain, create a simple structure
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
        """Process document bytes via Docling (PDF/Office).

        Stores full Docling JSON as ground truth (returned in result; caller may persist to artifact store).
        """
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
        except DoclingUnavailableError as e:
            return IngestResult(
                success=False,
                markdown_projection="",
                docling_json={},
                parse_profile=ParseProfile.PDF_TEXT,
                docling_schema_version="unknown",
                error_code=ErrorCode.DOC_PARSE_DEPENDENCY_MISSING,
                error_message=str(e),
            )
        except DoclingParseError as e:
            return IngestResult(
                success=False,
                markdown_projection="",
                docling_json={},
                parse_profile=ParseProfile.PDF_TEXT,
                docling_schema_version="unknown",
                error_code=ErrorCode.DOC_PARSE_FAILED,
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

        return IngestResult(
            success=True,
            markdown_projection=parsed.markdown_projection,
            docling_json=parsed.docling_json,
            parse_profile=parsed.parse_profile,
            docling_schema_version=parsed.docling_schema_version,
        )

    async def process_document(
        self, *, content: str | bytes, mime_type: str
    ) -> IngestResult:
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

                # Keep plain text/markdown lightweight and deterministic.
                parsed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                docling_json = {"content": text, "mime_type": mime_type, "parsed_at": parsed_at}
                return IngestResult(
                    success=True,
                    markdown_projection=text,
                    docling_json=docling_json,
                    parse_profile=ParseProfile.MARKDOWN if mime_type == "text/markdown" else ParseProfile.TEXT,
                    docling_schema_version="1.0",
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

            # Unknown type
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
