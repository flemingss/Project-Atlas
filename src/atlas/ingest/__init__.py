"""Ingest helpers (Docling integration, layout PDF parser, artifact handling helpers).

Phase 4 work lives here to keep pipeline node code small.
"""

from .types import LayoutType, PDFParseResult, ParsedRegion, TableResult  # noqa: F401
from .model_manager import ModelManager  # noqa: F401

try:
    from .pdf_parser import LayoutPdfParser  # noqa: F401
except ImportError:
    # Allow overall ingest module to import even if LayoutPdfParser unavailable (VLM-only)
    LayoutPdfParser = None  # type: ignore[assignment]
