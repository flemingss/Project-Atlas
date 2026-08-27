"""Shared types for the layout-aware PDF parser.

Ported from RAGFlow's deepdoc engine (Apache 2.0, InfiniFlow/ragflow).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class LayoutType(str, Enum):
    """Layout region types from ONNX layout model."""
    TEXT = "text"
    TITLE = "title"
    FIGURE = "figure"
    FIGURE_CAPTION = "figure caption"
    TABLE = "table"
    TABLE_CAPTION = "table caption"
    HEADER = "header"
    FOOTER = "footer"
    REFERENCE = "reference"
    EQUATION = "equation"


# Types considered noise — removed before text extraction
GARBAGE_LAYOUT_TYPES = frozenset({LayoutType.HEADER, LayoutType.FOOTER, LayoutType.REFERENCE})


@dataclass
class ParsedRegion:
    """A classified text region from the PDF parser."""
    layout_type: LayoutType
    text: str
    page_number: int
    x0: float = 0.0
    top: float = 0.0
    x1: float = 0.0
    bottom: float = 0.0
    confidence: float = 1.0


@dataclass
class TableResult:
    """Structured table extraction result."""
    html: str
    caption: str = ""
    page_number: int = 0
    confidence: float = 1.0


@dataclass
class PDFParseResult:
    """Complete result from layout-aware PDF parsing."""
    regions: list[ParsedRegion] = field(default_factory=list)
    tables: list[TableResult] = field(default_factory=list)
    markdown: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    page_count: int = 0
    mean_ocr_confidence: float = 1.0
    layout_confidence: float = 1.0
    ocr_coverage: float = 1.0
    estimated_is_scanned: bool = False
