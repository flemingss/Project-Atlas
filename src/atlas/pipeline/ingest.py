"""Ingest node for Project Atlas pipeline (HLD section 2: Ingest).

Handles document ingestion and conversion to DoclingDocument format.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from atlas.diagnostics import ErrorCode, get_diagnostics
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
            "parsed_at": datetime.utcnow().isoformat() + "Z",
        }

        return IngestResult(
            success=True,
            markdown_projection=text,
            docling_json=docling_json,
            parse_profile=ParseProfile.TEXT,
            docling_schema_version="1.0",
        )

    async def process_pdf(self, *, pdf_path: str) -> IngestResult:
        """Process PDF document using Docling.

        NOTE: This is a scaffold. Full implementation requires:
        - Docling library integration
        - PDF parsing with OCR support
        - Table and figure extraction
        - Layout analysis
        """
        self.diagnostics.log_warning(
            component="ingest",
            message="PDF processing not yet implemented - placeholder scaffold",
            context={"pdf_path": pdf_path},
        )

        # Placeholder return
        return IngestResult(
            success=False,
            markdown_projection="",
            docling_json={},
            parse_profile=ParseProfile.PDF_TEXT,
            docling_schema_version="1.0",
            error_code=ErrorCode.DOC_PARSE_FAILED,
            error_message="PDF processing not yet implemented",
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

            if mime_type == "application/pdf":
                self.diagnostics.log_error(
                    component="ingest",
                    error_code=ErrorCode.DOC_PARSE_FAILED,
                    message="PDF processing not yet implemented",
                )
                return IngestResult(
                    success=False,
                    markdown_projection="",
                    docling_json={},
                    parse_profile=ParseProfile.PDF_TEXT,
                    docling_schema_version="1.0",
                    error_code=ErrorCode.DOC_PARSE_FAILED,
                    error_message="PDF processing not yet implemented",
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
