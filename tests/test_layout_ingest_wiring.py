"""Tests for layout parser wiring in atlas.pipeline.ingest.

Exercises the IngestNode integration with the layout parser backend:
- backend selection (docling / layout / auto)
- _try_layout_parser graceful failure
- _apply_pdf_quality_gates accept/reject
- PDF_LAYOUT parse profile

Uses monkeypatching to avoid importing heavyweight deps (onnxruntime, etc.).
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from atlas.pipeline.ingest import IngestNode, IngestResult
from atlas.schemas import ParseProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _install_docling_mock(monkeypatch):
    """Mock ``parse_document_path`` so Docling path succeeds without real deps."""
    from atlas.ingest.docling_adapter import DoclingParseResult

    fake_result = DoclingParseResult(
        markdown_projection="# Test Document\n\nHello world content here with enough text.\n" * 5,
        docling_json={"test": True},
        parse_profile=ParseProfile.PDF_TEXT,
        docling_schema_version="1.0",
        meta={"fake": True},
    )
    import atlas.pipeline.ingest as mod
    monkeypatch.setattr(mod, "parse_document_path", lambda **kw: fake_result)


def _make_layout_result():
    """Create a fake layout IngestResult."""
    return IngestResult(
        success=True,
        markdown_projection="# Layout Result\n\nParsed via layout engine.\n" * 10,
        docling_json={"parser": "layout"},
        parse_profile=ParseProfile.PDF_LAYOUT,
        docling_schema_version="1.0",
        meta={
            "extraction_backend": "layout",
            "mean_ocr_confidence": 0.9,
            "layout_confidence": 0.92,
            "ocr_coverage": 0.85,
            "page_count": 3,
        },
    )


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

async def test_backend_docling_skips_layout(monkeypatch):
    """When backend='docling', the layout parser is never attempted."""
    _install_docling_mock(monkeypatch)
    node = IngestNode()
    monkeypatch.setattr(node.settings, "atlas_pdf_parser_backend", "docling")

    result = await node.process_doc_bytes(
        doc_bytes=b"%PDF-1.4 fake content",
        source_mime_type="application/pdf",
    )
    assert result.success is True
    assert result.parse_profile != ParseProfile.PDF_LAYOUT
    assert result.parse_profile == ParseProfile.PDF_TEXT


async def test_backend_layout_uses_layout_parser(monkeypatch):
    """When backend='layout' and layout parser succeeds, PDF_LAYOUT profile is used."""
    node = IngestNode()
    monkeypatch.setattr(node.settings, "atlas_pdf_parser_backend", "layout")

    # Mock _try_layout_parser to return a successful result
    monkeypatch.setattr(node, "_try_layout_parser", lambda *a, **kw: _make_layout_result())

    result = await node.process_doc_bytes(
        doc_bytes=b"%PDF-1.4 fake",
        source_mime_type="application/pdf",
    )
    assert result.success is True
    assert result.parse_profile == ParseProfile.PDF_LAYOUT


async def test_backend_layout_fails_no_fallback(monkeypatch):
    """When backend='layout' and layout parser fails, result is a failure (no Docling fallback)."""
    _install_docling_mock(monkeypatch)
    node = IngestNode()
    monkeypatch.setattr(node.settings, "atlas_pdf_parser_backend", "layout")

    # Mock _try_layout_parser to return None (failure)
    monkeypatch.setattr(node, "_try_layout_parser", lambda *a, **kw: None)

    result = await node.process_doc_bytes(
        doc_bytes=b"%PDF-1.4 fake",
        source_mime_type="application/pdf",
    )
    assert result.success is False
    assert "no fallback" in (result.error_message or "").lower()


async def test_backend_auto_falls_back_to_docling(monkeypatch):
    """When backend='auto' and layout parser fails, Docling is used as fallback."""
    _install_docling_mock(monkeypatch)
    node = IngestNode()
    monkeypatch.setattr(node.settings, "atlas_pdf_parser_backend", "auto")

    # Mock _try_layout_parser to return None (failure → triggers fallback)
    monkeypatch.setattr(node, "_try_layout_parser", lambda *a, **kw: None)

    result = await node.process_doc_bytes(
        doc_bytes=b"%PDF-1.4 fake",
        source_mime_type="application/pdf",
    )
    assert result.success is True
    assert result.parse_profile == ParseProfile.PDF_TEXT  # Docling's profile


async def test_backend_auto_prefers_layout(monkeypatch):
    """When backend='auto' and layout parser succeeds, layout result is used."""
    _install_docling_mock(monkeypatch)
    node = IngestNode()
    monkeypatch.setattr(node.settings, "atlas_pdf_parser_backend", "auto")

    monkeypatch.setattr(node, "_try_layout_parser", lambda *a, **kw: _make_layout_result())

    result = await node.process_doc_bytes(
        doc_bytes=b"%PDF-1.4 fake",
        source_mime_type="application/pdf",
    )
    assert result.success is True
    assert result.parse_profile == ParseProfile.PDF_LAYOUT


async def test_non_pdf_ignores_layout_parser(monkeypatch):
    """Non-PDF documents always go through Docling, regardless of backend setting."""
    _install_docling_mock(monkeypatch)
    node = IngestNode()
    monkeypatch.setattr(node.settings, "atlas_pdf_parser_backend", "layout")

    result = await node.process_doc_bytes(
        doc_bytes=b"PK\x03\x04 fake docx bytes",
        source_mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert result.success is True
    # Non-PDF→ Docling; profile should not be PDF_LAYOUT
    assert result.parse_profile != ParseProfile.PDF_LAYOUT


# ---------------------------------------------------------------------------
# _try_layout_parser graceful failure
# ---------------------------------------------------------------------------

def test_try_layout_parser_returns_none_on_import_error(monkeypatch):
    """_try_layout_parser should return None when the layout module can't be imported."""
    node = IngestNode()

    # Patch the import inside _try_layout_parser to fail
    import builtins
    real_import = builtins.__import__

    def fail_layout_import(name, *args, **kwargs):
        if "pdf_parser" in name:
            raise ImportError("mocked import failure")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_layout_import)
    result = node._try_layout_parser(b"%PDF-1.4 fake", "test.pdf")
    assert result is None


# ---------------------------------------------------------------------------
# Quality gates
# ---------------------------------------------------------------------------

def test_quality_gates_pass_good_content():
    """Good content with sufficient text passes quality gates."""
    node = IngestNode()
    result = IngestResult(
        success=True,
        markdown_projection="This is enough good content for the quality gates to pass. " * 20,
        docling_json={},
        parse_profile=ParseProfile.PDF_LAYOUT,
        docling_schema_version="1.0",
        meta={"extraction_backend": "layout"},
    )
    gated = node._apply_pdf_quality_gates(result, "application/pdf", None)
    assert gated.success is True


def test_quality_gates_reject_garbage():
    """Content that is mostly symbols/garbage should fail quality gates."""
    node = IngestNode()
    # Set strict quality gates
    node.settings.atlas_pdf_quality_alpha_ratio_min = 0.5
    node.settings.atlas_pdf_quality_min_chars = 0
    node.settings.atlas_pdf_quality_min_words = 0

    result = IngestResult(
        success=True,
        markdown_projection="!@#$%^&*()!@#$%^&*()" * 10,
        docling_json={},
        parse_profile=ParseProfile.PDF_LAYOUT,
        docling_schema_version="1.0",
        meta={},
    )
    gated = node._apply_pdf_quality_gates(result, "application/pdf", None)
    assert gated.success is False


def test_quality_gates_reject_short_content():
    """Very short content fails min_chars gate."""
    node = IngestNode()
    node.settings.atlas_pdf_quality_min_chars = 100
    node.settings.atlas_pdf_quality_min_words = 10

    result = IngestResult(
        success=True,
        markdown_projection="Hi",
        docling_json={},
        parse_profile=ParseProfile.PDF_TEXT,
        docling_schema_version="1.0",
        meta={},
    )
    gated = node._apply_pdf_quality_gates(result, "application/pdf", None)
    assert gated.success is False


def test_quality_gates_attach_quality_meta():
    """Passing quality gates should attach quality metrics to meta."""
    node = IngestNode()
    result = IngestResult(
        success=True,
        markdown_projection="Sufficient text content for quality check. " * 20,
        docling_json={},
        parse_profile=ParseProfile.PDF_LAYOUT,
        docling_schema_version="1.0",
        meta={"extraction_backend": "layout"},
    )
    gated = node._apply_pdf_quality_gates(result, "application/pdf", None)
    assert gated.success is True
    assert "quality" in (gated.meta or {})
    qm = gated.meta["quality"]
    assert "chars" in qm
    assert "alpha_ratio" in qm


# ---------------------------------------------------------------------------
# ParseProfile.PDF_LAYOUT
# ---------------------------------------------------------------------------

def test_pdf_layout_profile_exists():
    assert ParseProfile.PDF_LAYOUT.value == "pdf_layout"


def test_pdf_layout_profile_is_str():
    assert isinstance(ParseProfile.PDF_LAYOUT, str)
    assert ParseProfile.PDF_LAYOUT == "pdf_layout"
