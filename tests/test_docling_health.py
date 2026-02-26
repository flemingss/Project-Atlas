"""Tests for docling_health.compute_health scoring."""

from __future__ import annotations

from atlas.ingest.docling_health import DoclingHealthResult, compute_health


# ---------------------------------------------------------------------------
# Basic sanity
# ---------------------------------------------------------------------------

def test_compute_health_returns_result():
    result = compute_health(meta=None, markdown_length=500)
    assert isinstance(result, DoclingHealthResult)
    assert 1 <= result.health_score <= 5


def test_health_none_meta_defaults():
    """None meta should not crash; signals should still contain defaults."""
    result = compute_health(meta=None, markdown_length=100)
    assert result.signals["extraction_method"] == "unknown"
    assert result.signals["content_score"] >= 1


# ---------------------------------------------------------------------------
# Content volume signal
# ---------------------------------------------------------------------------

def test_health_empty_markdown():
    result = compute_health(meta={}, markdown_length=0)
    assert result.signals["content_score"] == 1
    assert any("empty" in w.lower() for w in result.warnings)


def test_health_short_markdown():
    result = compute_health(meta={}, markdown_length=30)
    assert result.signals["content_score"] == 2


def test_health_medium_markdown():
    result = compute_health(meta={}, markdown_length=500)
    assert result.signals["content_score"] == 4


def test_health_long_markdown():
    result = compute_health(meta={}, markdown_length=5000)
    assert result.signals["content_score"] == 5


# ---------------------------------------------------------------------------
# Extraction method signal
# ---------------------------------------------------------------------------

def test_health_embedded_text_method():
    meta = {"extraction_method": "embedded_text"}
    result = compute_health(meta=meta, markdown_length=500)
    assert result.signals["extraction_method_score"] == 5


def test_health_ocr_method_warns():
    meta = {"extraction_method": "ocr"}
    result = compute_health(meta=meta, markdown_length=500)
    assert result.signals["extraction_method_score"] == 3
    assert any("ocr" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# Rotation signal
# ---------------------------------------------------------------------------

def test_health_no_rotation():
    meta = {"pdf_preflight": {"has_rotation": False, "mixed_rotation": False}}
    result = compute_health(meta=meta, markdown_length=500)
    assert result.signals["rotation_score"] == 5


def test_health_uniform_rotation():
    meta = {"pdf_preflight": {"has_rotation": True, "mixed_rotation": False}}
    result = compute_health(meta=meta, markdown_length=500)
    assert result.signals["rotation_score"] == 3
    assert any("rotation" in w.lower() for w in result.warnings)


def test_health_mixed_rotation():
    meta = {"pdf_preflight": {"has_rotation": True, "mixed_rotation": True}}
    result = compute_health(meta=meta, markdown_length=500)
    assert result.signals["rotation_score"] == 2


# ---------------------------------------------------------------------------
# Text-as-shapes signal
# ---------------------------------------------------------------------------

def test_health_text_as_shapes():
    meta = {"pdf_preflight": {"text_as_shapes_suspected": True}}
    result = compute_health(meta=meta, markdown_length=500)
    assert result.signals["shapes_score"] == 1
    assert any("shapes" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# Composite score
# ---------------------------------------------------------------------------

def test_health_perfect_pdf():
    """Embedded text, decent length, no rotation, no shapes → score 5."""
    meta = {
        "extraction_method": "embedded_text",
        "pdf_preflight": {
            "has_rotation": False,
            "mixed_rotation": False,
            "text_as_shapes_suspected": False,
            "sample_text_chars": 5000,
        },
    }
    result = compute_health(meta=meta, markdown_length=3000)
    assert result.health_score == 5


def test_health_degraded_pdf():
    """OCR, short text, shapes → low composite."""
    meta = {
        "extraction_method": "ocr",
        "pdf_preflight": {
            "has_rotation": True,
            "mixed_rotation": True,
            "text_as_shapes_suspected": True,
            "sample_text_chars": 5,
        },
    }
    result = compute_health(meta=meta, markdown_length=10)
    assert result.health_score <= 2


def test_health_to_dict():
    result = compute_health(meta=None, markdown_length=500)
    d = result.to_dict()
    assert "health_score" in d
    assert "signals" in d
    assert "warnings" in d
