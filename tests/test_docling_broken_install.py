"""Tests for distinguishing a missing Docling install from a broken one.

Covers P0-02:
- (a) top-level ``docling`` genuinely absent → DoclingUnavailableError
    ("not installed", DOC_PARSE_DEPENDENCY_MISSING).
- (b) docling present but broken (transitive import failure, missing submodule,
    AttributeError/TypeError on a partial tree) → DoclingBrokenInstallError
    naming the underlying exception.
- Explicit ``backend=docling`` must fail loudly on a broken install.
- ``backend=auto`` may fall back for absence, but must loudly warn and tag the
    fallback when the install is broken.
"""

from __future__ import annotations

import builtins
from typing import Any

import pytest

import atlas.pipeline.parsers as pipeline_parsers
from atlas.ingest.docling_adapter import (
    DoclingBrokenInstallError,
    DoclingUnavailableError,
    _classify_docling_import_error,
)
from atlas.pipeline.ingest import IngestNode
from atlas.pipeline.parsers import LayoutParser
from atlas.schemas import ParseProfile


def _patch_docling_import(monkeypatch: Any, exc: BaseException) -> None:
    """Force ``import docling...`` inside the adapter to raise ``exc``."""
    real_import = builtins.__import__

    def fail_docling_import(name, *args, **kwargs):
        if name == "docling" or name.startswith("docling."):
            raise exc
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_docling_import)


# ---------------------------------------------------------------------------
# Import classification (adapter level)
# ---------------------------------------------------------------------------

def test_classify_absent_top_level_raises_unavailable() -> None:
    """ModuleNotFoundError for top-level 'docling' → Unavailable, message says not installed."""
    err = ModuleNotFoundError("No module named 'docling'", name="docling")
    with pytest.raises(DoclingUnavailableError) as exc_info:
        _classify_docling_import_error(err)
    assert "not installed" in str(exc_info.value)


def test_classify_transitive_dependency_raises_broken() -> None:
    """ModuleNotFoundError for a *transitive* dep → Broken, message is distinct and loud."""
    err = ModuleNotFoundError("No module named 'docling.models.tableformer'", name="docling.models.tableformer")
    with pytest.raises(DoclingBrokenInstallError) as exc_info:
        _classify_docling_import_error(err)
    msg = str(exc_info.value)
    assert "not installed" not in msg
    assert "broken" in msg
    assert "tableformer" in msg  # names the underlying exception


def test_classify_partial_tree_missing_submodule_raises_broken() -> None:
    """docling.pipeline present but docling.document_converter missing → Broken."""
    err = ModuleNotFoundError("No module named 'docling.document_converter'", name="docling.document_converter")
    with pytest.raises(DoclingBrokenInstallError) as exc_info:
        _classify_docling_import_error(err)
    assert "broken" in str(exc_info.value)


def test_classify_non_import_error_raises_broken() -> None:
    """AttributeError/TypeError from a partial tree → Broken (not Unavailable)."""
    err = AttributeError("module 'docling' has no attribute 'DocumentConverter'")
    with pytest.raises(DoclingBrokenInstallError) as exc_info:
        _classify_docling_import_error(err)
    msg = str(exc_info.value)
    assert "broken" in msg
    assert "AttributeError" in msg


def test_broken_install_message_is_distinct() -> None:
    """The broken message must not read as 'not installed' and must chain the cause."""
    assert "not installed" in str(DoclingUnavailableError())
    broken = DoclingBrokenInstallError(underlying=ModuleNotFoundError("boom"))
    assert "broken" in str(broken)
    assert "not installed" not in str(broken)


# ---------------------------------------------------------------------------
# Import through the adapter: absent → Unavailable; broken → BrokenInstall
# ---------------------------------------------------------------------------

def test_adapter_import_absent_raises_unavailable(monkeypatch: Any) -> None:
    """Import-time absence of top-level docling inside parse → DoclingUnavailableError."""
    from pathlib import Path

    from atlas.ingest.docling_adapter import parse_document_path

    _patch_docling_import(monkeypatch, ModuleNotFoundError("No module named 'docling'", name="docling"))
    with pytest.raises(DoclingUnavailableError):
        parse_document_path(doc_path=Path("/tmp/x.pdf"), source_mime_type="application/pdf")


def test_adapter_import_broken_raises_broken_install(monkeypatch: Any) -> None:
    """Transitive/submodule failure inside parse → DoclingBrokenInstallError."""
    from pathlib import Path

    from atlas.ingest.docling_adapter import parse_document_path

    _patch_docling_import(
        monkeypatch,
        ModuleNotFoundError("No module named 'docling.document_converter'", name="docling.document_converter"),
    )
    with pytest.raises(DoclingBrokenInstallError):
        parse_document_path(doc_path=Path("/tmp/x.pdf"), source_mime_type="application/pdf")


# ---------------------------------------------------------------------------
# Explicit backend: broken install must fail loudly (raise), never fall back
# ---------------------------------------------------------------------------

async def test_explicit_backend_broken_raises_and_never_falls_back_to_layout(monkeypatch: Any) -> None:
    """backend=docling with a broken install → IngestResult failure tagged broken, layout untouched."""
    node = IngestNode()
    monkeypatch.setattr(node.settings, "atlas_pdf_parser_backend", "docling")

    def _broken_parse(*, doc_path, source_mime_type, table_extraction=True):
        raise DoclingBrokenInstallError(underlying=ModuleNotFoundError("No module named 'docling.core'", name="docling.core"))

    monkeypatch.setattr(pipeline_parsers, "parse_document_path", _broken_parse)

    layout_called = False

    async def _fake_layout_parse(self, doc_bytes, source_mime_type, filename):
        nonlocal layout_called
        layout_called = True

    monkeypatch.setattr(LayoutParser, "parse", _fake_layout_parse)

    result = await node.process_doc_bytes(
        doc_bytes=b"%PDF-1.4 fake",
        source_mime_type="application/pdf",
    )
    assert result.success is False
    assert result.meta is not None and result.meta.get("docling_install_state") == "broken"
    assert "broken" in (result.error_message or "")
    assert layout_called is False


async def test_explicit_backend_absent_uses_plain_unavailable_message(monkeypatch: Any) -> None:
    """backend=docling with genuinely-absent docling keeps the 'not installed' message."""
    node = IngestNode()
    monkeypatch.setattr(node.settings, "atlas_pdf_parser_backend", "docling")

    def _absent_parse(*, doc_path, source_mime_type, table_extraction=True):
        raise DoclingUnavailableError()

    monkeypatch.setattr(pipeline_parsers, "parse_document_path", _absent_parse)

    result = await node.process_doc_bytes(
        doc_bytes=b"%PDF-1.4 fake",
        source_mime_type="application/pdf",
    )
    assert result.success is False
    # Absence must NOT be tagged as broken — it's the ordinary missing-dep path.
    assert (result.meta or {}).get("docling_install_state") != "broken"
    assert "not installed" in (result.error_message or "")


# ---------------------------------------------------------------------------
# Auto backend: broken install warns loudly; absence falls back quietly
# ---------------------------------------------------------------------------

async def test_auto_backend_broken_falls_back_with_loud_warning(monkeypatch: Any) -> None:
    """backend=auto + broken docling → layout fallback used, meta tags broken, diagnostics log_error."""
    node = IngestNode()
    monkeypatch.setattr(node.settings, "atlas_pdf_parser_backend", "auto")

    def _broken_parse(*, doc_path, source_mime_type, table_extraction=True):
        raise DoclingBrokenInstallError(underlying=TypeError("partial tree"))

    monkeypatch.setattr(pipeline_parsers, "parse_document_path", _broken_parse)

    async def _ok_layout_parse(self, doc_bytes, source_mime_type, filename):
        from atlas.pipeline.ingest import IngestResult

        return IngestResult(
            success=True,
            markdown_projection="# Layout\n\nLayout text sufficient for gates. " * 10,
            docling_json={},
            parse_profile=ParseProfile.PDF_LAYOUT,
            docling_schema_version="1.0",
            meta={"extraction_backend": "layout", "mean_ocr_confidence": 0.9},
        )

    monkeypatch.setattr(LayoutParser, "parse", _ok_layout_parse)

    log_error_calls: list[dict[str, Any]] = []

    def _capture_log_error(*, component, error_code, message, context=None, exception=None):
        log_error_calls.append({"message": message, "context": context})

    monkeypatch.setattr(node.diagnostics, "log_error", _capture_log_error)

    result = await node.process_doc_bytes(
        doc_bytes=b"%PDF-1.4 fake",
        source_mime_type="application/pdf",
    )
    assert result.success is True
    assert result.meta is not None
    assert result.meta.get("extraction_backend") == "layout"
    assert result.meta.get("docling_install_state") == "broken"
    # A prominent diagnostics error (not just a debug log) must have fired.
    assert any("DEGRADED" in c["message"] for c in log_error_calls)


async def test_auto_backend_absent_falls_back_quietly(monkeypatch: Any) -> None:
    """backend=auto + genuinely-absent docling → clean fallback, no loud broken warning."""
    node = IngestNode()
    monkeypatch.setattr(node.settings, "atlas_pdf_parser_backend", "auto")

    def _absent_parse(*, doc_path, source_mime_type, table_extraction=True):
        raise DoclingUnavailableError()

    monkeypatch.setattr(pipeline_parsers, "parse_document_path", _absent_parse)

    async def _ok_layout_parse(self, doc_bytes, source_mime_type, filename):
        from atlas.pipeline.ingest import IngestResult

        return IngestResult(
            success=True,
            markdown_projection="# Layout\n\nLayout text sufficient for gates. " * 10,
            docling_json={},
            parse_profile=ParseProfile.PDF_LAYOUT,
            docling_schema_version="1.0",
            meta={"extraction_backend": "layout", "mean_ocr_confidence": 0.9},
        )

    monkeypatch.setattr(LayoutParser, "parse", _ok_layout_parse)

    log_error_calls: list[dict[str, Any]] = []

    def _capture_log_error(*, component, error_code, message, context=None, exception=None):
        log_error_calls.append({"message": message, "context": context})

    monkeypatch.setattr(node.diagnostics, "log_error", _capture_log_error)

    result = await node.process_doc_bytes(
        doc_bytes=b"%PDF-1.4 fake",
        source_mime_type="application/pdf",
    )
    assert result.success is True
    assert result.meta is not None and result.meta.get("extraction_backend") == "layout"
    # Absence is a fine, quiet fallback — no "DEGRADED" loud warning.
    assert not any("DEGRADED" in c["message"] for c in log_error_calls)
    assert (result.meta or {}).get("docling_install_state") != "broken"
