"""Ingest helpers (Docling integration, layout PDF parser, artifact handling helpers).

Phase 4 work lives here to keep pipeline node code small.
"""

from .model_manager import ModelManager  # noqa: F401
from .types import LayoutType, ParsedRegion, PDFParseResult, TableResult  # noqa: F401

try:
    from .pdf_parser import LayoutPdfParser
except ImportError:
    # Allow overall ingest module to import even if LayoutPdfParser unavailable (VLM-only)
    LayoutPdfParser = None  # type: ignore[assignment]
