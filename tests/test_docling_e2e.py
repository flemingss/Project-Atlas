"""End-to-end tests that run Docling for real.

Every other Docling test in this suite monkeypatches ``parse_document_path``,
so they cover the wiring around the parser — artifact persistence, error codes,
fidelity flags — while never exercising Docling itself. A Docling upgrade, a
model change, or a pipeline-option regression would pass all of them.

These tests close that gap. They build a PDF with known ground truth and assert
that the real converter recovers it.

**They assert a quality floor, not exact output.** Docling's markdown changes
between versions (heading levels, table syntax, spacing), and pinning the exact
string would make every upgrade look like a regression. What is pinned is what
must be true for the ingest to be worth anything: the words survive, the
structure is recognisable, and the metadata says what actually happened.

Marked ``integration`` and skipped unless the Docling models are already in the
local cache — so this runs in the container (which bakes them in) and never
triggers a multi-hundred-megabyte download inside CI.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from atlas.ingest.docling_adapter import (
    DoclingLimitsError,
    parse_document_path,
)

# ---------------------------------------------------------------------------
# Fixture construction — synthetic documents with known ground truth.
#
# Deliberately generated rather than committed: a checked-in PDF drifts out of
# sync with what the assertions claim it contains, and the real manuals this
# appliance ingests are production material that does not belong in the repo.
# ---------------------------------------------------------------------------

TITLE = "Atlas Ingest Verification Document"
SECTIONS = [
    ("1. Overview", "The reference oscillator maintains holdover during GPS signal loss."),
    ("2. Specifications", "Frequency stability is 1E-11 averaged over 24 hours."),
    ("3. Procedure", "Disconnect the antenna before replacing the module."),
]
TABLE_ROWS = [
    ["Parameter", "Value", "Unit"],
    ["Holdover", "24", "hours"],
    ["Stability", "1E-11", "fractional"],
]


def _models_cached() -> bool:
    """True when Docling's models are already on disk (no network needed)."""
    hub = Path.home() / ".cache" / "huggingface" / "hub"
    if not hub.is_dir():
        return False
    names = {p.name for p in hub.iterdir()}
    return any("docling" in n for n in names)


def _requires_docling() -> None:
    pytest.importorskip("docling", reason="docling not installed")
    pytest.importorskip("fitz", reason="PyMuPDF needed to build the fixture")
    if not _models_cached():
        pytest.skip("Docling models not cached locally — skipping to avoid a large download")


def _write_text_pdf(path: Path) -> None:
    """A born-digital PDF: headings and body as selectable text."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    y = 72.0
    page.insert_text((72, y), TITLE, fontsize=20)
    y += 36
    for heading, body in SECTIONS:
        page.insert_text((72, y), heading, fontsize=14)
        y += 22
        page.insert_text((72, y), body, fontsize=11)
        y += 30
    doc.save(str(path))
    doc.close()


def _write_table_pdf(path: Path) -> None:
    """A PDF whose content is laid out as a ruled table."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    x0, y0, col_w, row_h = 72.0, 100.0, 140.0, 28.0

    for r, row in enumerate(TABLE_ROWS):
        for c, cell in enumerate(row):
            x = x0 + c * col_w
            y = y0 + r * row_h
            page.draw_rect(fitz.Rect(x, y, x + col_w, y + row_h))
            page.insert_text((x + 6, y + 18), cell, fontsize=11)

    doc.save(str(path))
    doc.close()


def _write_many_page_pdf(path: Path, pages: int) -> None:
    import fitz

    doc = fitz.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"Page {i}", fontsize=11)
    doc.save(str(path))
    doc.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_docling_recovers_headings_and_body_from_born_digital_pdf(tmp_path: Path) -> None:
    """The quality floor: nothing the document said may go missing."""
    _requires_docling()

    pdf = tmp_path / "text.pdf"
    _write_text_pdf(pdf)

    result = parse_document_path(doc_path=pdf, source_mime_type="application/pdf")
    md = result.markdown_projection

    assert TITLE in md
    for heading, body in SECTIONS:
        assert heading in md, f"heading lost: {heading}"
        assert body in md, f"body text lost under {heading}"

    # Structure, not exact level: Docling has moved headings between h1/h2
    # across releases, and that is not a quality regression.
    assert md.lstrip().startswith("#"), "no markdown heading structure produced"

    # Order must be preserved — chunking and citations depend on it.
    positions = [md.index(h) for h, _ in SECTIONS]
    assert positions == sorted(positions), "section order scrambled"


@pytest.mark.integration
def test_docling_takes_the_embedded_text_path_when_text_is_selectable(tmp_path: Path) -> None:
    """A born-digital PDF must not be sent through OCR.

    OCR on selectable text is slower and strictly lossier. The adapter decides
    this from preflight, and the decision is recorded in meta — this pins it.
    """
    _requires_docling()

    pdf = tmp_path / "text.pdf"
    _write_text_pdf(pdf)

    result = parse_document_path(doc_path=pdf, source_mime_type="application/pdf")

    assert result.meta.get("extraction_method") == "embedded_text"
    assert result.meta.get("converter") == "docling"
    assert int((result.meta.get("pdf_preflight") or {}).get("pages", 0)) == 1


@pytest.mark.integration
def test_docling_preserves_table_cell_content(tmp_path: Path) -> None:
    """Cell values must survive.

    Deliberately not asserting markdown table syntax: whether Docling emits a
    pipe table, HTML, or plain lines varies by version and by whether
    TableFormer fires. Losing the numbers would be a real regression; changing
    how they are formatted is not.
    """
    _requires_docling()

    pdf = tmp_path / "table.pdf"
    _write_table_pdf(pdf)

    result = parse_document_path(doc_path=pdf, source_mime_type="application/pdf")
    md = result.markdown_projection

    # Case-insensitive on purpose. Docling 2.76 rewrites `1E-11` as `1e-11` in
    # table cells — it does not reproduce source text byte-for-byte. That is
    # semantically harmless for exponent notation, but it means table content
    # must never be compared exactly, and it is worth knowing that cell text is
    # normalised at all.
    lowered = md.lower()
    for row in TABLE_ROWS:
        for cell in row:
            assert cell.lower() in lowered, f"table cell lost: {cell!r}"


@pytest.mark.integration
def test_table_extraction_knob_actually_changes_the_output(tmp_path: Path) -> None:
    """`pdf_parser.table_extraction` must have an effect.

    It was documented in pipeline.yaml and read by nothing, so an operator
    could set it either way and get identical behaviour. Now it reaches
    Docling's `do_table_structure`.

    The difference is worth knowing: with structure recognition on, rows are
    separated into real columns; with it off the whole table collapses onto one
    line — roughly 6x faster, and useless for anything that needs to cite a
    specific cell.
    """
    _requires_docling()

    pdf = tmp_path / "table.pdf"
    _write_table_pdf(pdf)

    structured = parse_document_path(
        doc_path=pdf, source_mime_type="application/pdf", table_extraction=True
    ).markdown_projection
    flattened = parse_document_path(
        doc_path=pdf, source_mime_type="application/pdf", table_extraction=False
    ).markdown_projection

    def _rows_are_separate(md: str) -> bool:
        """True when the two data rows land on different lines."""
        return any(
            "Holdover" in line and "Stability" not in line
            for line in md.splitlines()
        )

    assert _rows_are_separate(structured), "structure recognition did not separate rows"
    assert not _rows_are_separate(flattened), (
        "disabling table_extraction changed nothing — the knob is inert again"
    )


@pytest.mark.integration
def test_docling_emits_a_structured_document_json(tmp_path: Path) -> None:
    """docling_json is persisted as an artifact and read back by the editor."""
    _requires_docling()

    pdf = tmp_path / "text.pdf"
    _write_text_pdf(pdf)

    result = parse_document_path(doc_path=pdf, source_mime_type="application/pdf")

    assert isinstance(result.docling_json, dict)
    assert result.docling_json, "docling_json is empty — editor page mapping would break"
    assert result.docling_schema_version


def test_page_limit_is_enforced_before_any_parsing(tmp_path: Path, monkeypatch) -> None:
    """The cap must reject early, off preflight — no models required.

    Runs unconditionally: this is the guard that keeps an oversized document
    from reaching the converter at all, and it should not depend on whether
    Docling's weights happen to be cached.
    """
    pytest.importorskip("fitz", reason="PyMuPDF needed to build the fixture")
    monkeypatch.setenv("ATLAS_PDF_MAX_PAGES", "3")

    pdf = tmp_path / "many.pdf"
    _write_many_page_pdf(pdf, pages=5)

    with pytest.raises(DoclingLimitsError) as exc:
        parse_document_path(doc_path=pdf, source_mime_type="application/pdf")
    assert "page limit" in str(exc.value).lower()
