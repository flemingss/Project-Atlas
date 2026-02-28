"""Tests for atlas.ingest.types — layout-aware PDF parser data types."""

from __future__ import annotations

from atlas.ingest.types import (
    GARBAGE_LAYOUT_TYPES,
    LayoutBox,
    LayoutType,
    OCRBox,
    PDFParseResult,
    ParsedRegion,
    TableResult,
)


# ---------------------------------------------------------------------------
# LayoutType enum
# ---------------------------------------------------------------------------

def test_layout_type_enum_has_10_members():
    assert len(LayoutType) == 10


def test_layout_type_text():
    assert LayoutType.TEXT.value == "text"


def test_layout_type_title():
    assert LayoutType.TITLE.value == "title"


def test_layout_type_figure():
    assert LayoutType.FIGURE.value == "figure"


def test_layout_type_figure_caption():
    assert LayoutType.FIGURE_CAPTION.value == "figure caption"


def test_layout_type_table():
    assert LayoutType.TABLE.value == "table"


def test_layout_type_table_caption():
    assert LayoutType.TABLE_CAPTION.value == "table caption"


def test_layout_type_header():
    assert LayoutType.HEADER.value == "header"


def test_layout_type_footer():
    assert LayoutType.FOOTER.value == "footer"


def test_layout_type_reference():
    assert LayoutType.REFERENCE.value == "reference"


def test_layout_type_equation():
    assert LayoutType.EQUATION.value == "equation"


def test_layout_type_is_str_enum():
    """LayoutType members should be usable as plain strings."""
    assert isinstance(LayoutType.TEXT, str)
    assert LayoutType.TEXT == "text"


# ---------------------------------------------------------------------------
# GARBAGE_LAYOUT_TYPES
# ---------------------------------------------------------------------------

def test_garbage_layout_types_is_frozenset():
    assert isinstance(GARBAGE_LAYOUT_TYPES, frozenset)


def test_garbage_layout_types_contains_header_footer_reference():
    assert GARBAGE_LAYOUT_TYPES == frozenset({
        LayoutType.HEADER,
        LayoutType.FOOTER,
        LayoutType.REFERENCE,
    })


def test_garbage_layout_types_has_three_members():
    assert len(GARBAGE_LAYOUT_TYPES) == 3


def test_text_not_in_garbage():
    assert LayoutType.TEXT not in GARBAGE_LAYOUT_TYPES


def test_table_not_in_garbage():
    assert LayoutType.TABLE not in GARBAGE_LAYOUT_TYPES


# ---------------------------------------------------------------------------
# Dataclass instantiation
# ---------------------------------------------------------------------------

def test_layout_box_instantiation():
    box = LayoutBox(
        x0=0.0, top=0.0, x1=100.0, bottom=50.0,
        layout_type=LayoutType.TEXT, score=0.95, page_number=1,
    )
    assert box.x0 == 0.0
    assert box.x1 == 100.0
    assert box.layout_type == LayoutType.TEXT
    assert box.score == 0.95
    assert box.page_number == 1
    assert box.text == ""       # default
    assert box.layoutno == ""   # default


def test_layout_box_with_text():
    box = LayoutBox(
        x0=10.0, top=20.0, x1=300.0, bottom=80.0,
        layout_type=LayoutType.TITLE, score=0.99, page_number=0,
        text="Chapter 1", layoutno="t_0",
    )
    assert box.text == "Chapter 1"
    assert box.layoutno == "t_0"


def test_ocr_box_instantiation():
    box = OCRBox(x0=5.0, top=10.0, x1=200.0, bottom=40.0, text="Hello")
    assert box.text == "Hello"
    assert box.score == 1.0        # default
    assert box.page_number == 0    # default
    assert box.layout_type == ""   # default
    assert box.layoutno == ""      # default


def test_parsed_region_instantiation():
    region = ParsedRegion(
        layout_type=LayoutType.EQUATION,
        text="E = mc^2",
        page_number=3,
    )
    assert region.layout_type == LayoutType.EQUATION
    assert region.text == "E = mc^2"
    assert region.confidence == 1.0  # default


def test_table_result_instantiation():
    table = TableResult(html="<table><tr><td>A</td></tr></table>")
    assert table.html.startswith("<table>")
    assert table.caption == ""        # default
    assert table.page_number == 0     # default
    assert table.confidence == 1.0    # default


def test_table_result_with_caption():
    table = TableResult(
        html="<table></table>", caption="Revenue 2024", page_number=5, confidence=0.88,
    )
    assert table.caption == "Revenue 2024"
    assert table.confidence == 0.88


def test_pdf_parse_result_defaults():
    result = PDFParseResult()
    assert result.regions == []
    assert result.tables == []
    assert result.markdown == ""
    assert result.metadata == {}
    assert result.page_count == 0
    assert result.mean_ocr_confidence == 1.0
    assert result.layout_confidence == 1.0
    assert result.ocr_coverage == 1.0
    assert result.estimated_is_scanned is False


def test_pdf_parse_result_with_data():
    region = ParsedRegion(layout_type=LayoutType.TEXT, text="Hello", page_number=1)
    table = TableResult(html="<table></table>")
    result = PDFParseResult(
        regions=[region],
        tables=[table],
        markdown="# Hello\n\nWorld",
        metadata={"source": "test"},
        page_count=3,
        mean_ocr_confidence=0.85,
        layout_confidence=0.90,
        ocr_coverage=0.75,
        estimated_is_scanned=True,
    )
    assert len(result.regions) == 1
    assert len(result.tables) == 1
    assert result.page_count == 3
    assert result.estimated_is_scanned is True
    assert result.mean_ocr_confidence == 0.85
