"""Tests for atlas.api_vlm_ingest — VLM ingest API endpoints.

Uses mocked VLM calls and in-memory PDF bytes to test the full
endpoint lifecycle without external dependencies.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.vlm_ingest.session import (
    PageStatus,
    SessionStatus,
    VlmIngestConfig,
    SessionRegistry,
)
from atlas.vlm_ingest.stitcher import PageResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Minimal valid PDF (1 blank page) for PyMuPDF
_MINIMAL_PDF = (
    b"%PDF-1.0\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
    b"0000000058 00000 n \n0000000115 00000 n \n"
    b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF"
)


# ---------------------------------------------------------------------------
# Session lifecycle (no VLM calls)
# ---------------------------------------------------------------------------


class TestSessionLifecycle:
    """Test session create/get/list/delete via the registry directly."""

    def test_create_from_pdf_bytes(self):
        reg = SessionRegistry()
        from atlas.ingest.page_renderer import page_count
        n = page_count(_MINIMAL_PDF)
        s = reg.create(pdf_bytes=_MINIMAL_PDF, page_count=n, source_filename="test.pdf")
        assert s.page_count == n
        assert s.source_filename == "test.pdf"
        assert s.status == SessionStatus.CONFIGURING

    def test_config_overrides_applied(self):
        reg = SessionRegistry()
        cfg = VlmIngestConfig(dpi=300, crop_top=0.1, page_overrides={0: {"dpi": 400}})
        s = reg.create(pdf_bytes=_MINIMAL_PDF, page_count=1, config=cfg)
        ps = s.config.settings_for_page(0)
        assert ps.dpi == 400
        assert ps.crop_top == 0.1

    def test_headless_flag(self):
        reg = SessionRegistry()
        s = reg.create(pdf_bytes=_MINIMAL_PDF, page_count=1, headless=True)
        assert s.headless is True


# ---------------------------------------------------------------------------
# VLM parse (headless path in IngestNode)
# ---------------------------------------------------------------------------


class TestVlmParseHeadless:
    """Test the _vlm_parse method on IngestNode without real VLM calls."""

    @pytest.mark.asyncio
    async def test_vlm_parse_no_vision_model(self, monkeypatch):
        """When vision_model is not configured, return an error IngestResult."""
        from atlas.pipeline.ingest import IngestNode

        # Ensure config dir has no vision_model
        monkeypatch.setenv("ATLAS_CONFIG_DIR", ".")
        monkeypatch.setenv("ATLAS_MODELS_DIR", ".")

        node = IngestNode(pdf_parser_config={"backend": "vision"})

        # Patch ConfigManager at its source module (it's imported locally inside _vlm_parse)
        with patch("atlas.config_manager.ConfigManager") as MockCM:
            mock_cm = MagicMock()
            mock_cfg = MagicMock()
            mock_cfg.models = {"providers": {}, "roles": {}}
            mock_cm.get.return_value = mock_cfg
            MockCM.return_value = mock_cm

            result = await node._vlm_parse(_MINIMAL_PDF, "application/pdf", "test.pdf")
            assert not result.success
            assert "vision_model" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_vlm_parse_success(self, monkeypatch):
        """With a mocked vision model, _vlm_parse should produce stitched markdown."""
        from atlas.pipeline.ingest import IngestNode

        monkeypatch.setenv("ATLAS_CONFIG_DIR", ".")
        monkeypatch.setenv("ATLAS_MODELS_DIR", ".")
        monkeypatch.setenv("ATLAS_OPENAI_BASE_URL", "http://localhost:1234/v1")

        node = IngestNode(pdf_parser_config={"backend": "vision"})

        mock_provider = AsyncMock()
        mock_provider.chat = AsyncMock(return_value="# Title\n\nPage content here.")

        with patch("atlas.config_manager.ConfigManager") as MockCM, \
             patch("atlas.llm.registry.ModelRegistry") as MockReg:

            mock_cm = MagicMock()
            mock_cfg = MagicMock()
            mock_cfg.models = {
                "providers": {"lmstudio": {"type": "openai_compat"}},
                "roles": {"vision_model": {"provider": "lmstudio", "model_name": "test-vlm", "params": {}}},
            }
            mock_cm.get.return_value = mock_cfg
            MockCM.return_value = mock_cm

            mock_reg_inst = MagicMock()
            mock_resolved = MagicMock()
            mock_resolved.model_name = "test-vlm"
            mock_resolved.provider_name = "lmstudio"
            mock_resolved.params = {}
            mock_reg_inst.resolve.return_value = mock_resolved
            mock_reg_inst.provider_for.return_value = mock_provider
            MockReg.return_value = mock_reg_inst

            result = await node._vlm_parse(_MINIMAL_PDF, "application/pdf", "test.pdf")

        assert result.success
        assert "Title" in result.markdown_projection
        assert result.meta is not None
        assert result.meta.get("extraction_backend") == "vision"
        assert result.meta.get("vlm_model") == "test-vlm"
        mock_provider.chat.assert_called_once()


# ---------------------------------------------------------------------------
# Backend selection in IngestNode
# ---------------------------------------------------------------------------


class TestVisionBackendSelection:
    """Test that backend=vision routes to _vlm_parse."""

    @pytest.mark.asyncio
    async def test_backend_vision_calls_vlm_parse(self, monkeypatch):
        from atlas.pipeline.ingest import IngestNode

        monkeypatch.setenv("ATLAS_CONFIG_DIR", ".")
        monkeypatch.setenv("ATLAS_MODELS_DIR", ".")

        node = IngestNode(pdf_parser_config={"backend": "vision"})

        # Mock the _vlm_parse method
        mock_result = MagicMock()
        mock_result.success = True
        node._vlm_parse = AsyncMock(return_value=mock_result)

        result = await node.process_doc_bytes(
            doc_bytes=_MINIMAL_PDF,
            source_mime_type="application/pdf",
            filename="test.pdf",
        )

        node._vlm_parse.assert_called_once_with(_MINIMAL_PDF, "application/pdf", "test.pdf")
        assert result.success


# ---------------------------------------------------------------------------
# Stitcher integration with session
# ---------------------------------------------------------------------------


class TestSessionStitchIntegration:
    """Test session → stitch → commit flow end-to-end."""

    def test_full_workflow(self):
        from atlas.ingest.page_renderer import page_count
        reg = SessionRegistry()
        n = page_count(_MINIMAL_PDF)
        s = reg.create(pdf_bytes=_MINIMAL_PDF, page_count=n, source_filename="test.pdf", run_id=999)

        # Process page
        s.set_page_result(0, PageResult(
            page_num=0,
            markdown="# Title\n\nContent from page 0",
            model="test-vlm",
            dpi=200,
        ))

        assert s.all_done()

        # Stitch
        result = s.stitch()
        assert "Content from page 0" in result.markdown
        assert result.pages_processed == 1
        assert s.status == SessionStatus.COMPLETE

    def test_export_config_for_reuse(self):
        reg = SessionRegistry()
        cfg = VlmIngestConfig(dpi=300, crop_top=0.1, system_prompt="Custom prompt")
        s = reg.create(pdf_bytes=_MINIMAL_PDF, page_count=1, config=cfg)
        exported = s.config.to_dict()
        # Reimport
        cfg2 = VlmIngestConfig.from_dict(exported)
        assert cfg2.dpi == 300
        assert cfg2.crop_top == 0.1
        assert cfg2.system_prompt == "Custom prompt"


# ---------------------------------------------------------------------------
# Bulk process-all (session-level)
# ---------------------------------------------------------------------------


class TestBulkProcessAll:
    """Test bulk page processing via session registry (no HTTP)."""

    def test_bulk_marks_all_pages_done(self):
        from atlas.ingest.page_renderer import page_count
        reg = SessionRegistry()
        n = page_count(_MINIMAL_PDF)
        s = reg.create(pdf_bytes=_MINIMAL_PDF, page_count=n, source_filename="bulk.pdf")

        # Simulate what process-all does: iterate enabled, set result
        for p in s.enabled_pages():
            s.set_page_result(p, PageResult(
                page_num=p,
                markdown=f"# Page {p}\n\nContent {p}",
                model="test-vlm",
                dpi=200,
            ))

        assert s.all_done()
        result = s.stitch()
        assert result.pages_processed == n
        assert "Page 0" in result.markdown

    def test_bulk_skips_disabled_pages(self):
        from atlas.ingest.page_renderer import page_count
        reg = SessionRegistry()
        n = page_count(_MINIMAL_PDF)
        s = reg.create(pdf_bytes=_MINIMAL_PDF, page_count=n, source_filename="bulk.pdf")

        # Disable all pages
        for p in range(n):
            s.config.page_overrides[p] = {"enabled": False}

        assert s.enabled_pages() == []

    def test_bulk_errors_dont_block_stitch(self):
        """If some pages error, remaining done pages can still stitch."""
        reg = SessionRegistry()
        s = reg.create(pdf_bytes=_MINIMAL_PDF, page_count=1, source_filename="bulk.pdf")

        s.set_page_error(0, "VLM timeout")
        assert s.page_statuses[0] == PageStatus.ERROR
        # No pages done → stitch should produce empty
        assert not any(st == PageStatus.DONE for st in s.page_statuses.values())


# ---------------------------------------------------------------------------
# Default prompt includes heading formatting rules
# ---------------------------------------------------------------------------


class TestHeadingFormattingPrompt:
    """Verify the default VLM prompt includes heading hierarchy rules."""

    def test_default_prompt_has_numbered_sections(self):
        from atlas.ingest.page_renderer import build_vision_messages
        msgs = build_vision_messages(
            page_image_uri="data:image/png;base64,iVBOR",
            current_markdown="",
        )
        system = msgs[0]["content"]
        assert "# 1" in system
        assert "## 1.1" in system
        assert "### 1.1.1" in system

    def test_default_prompt_has_appendix_sections(self):
        from atlas.ingest.page_renderer import build_vision_messages
        msgs = build_vision_messages(
            page_image_uri="data:image/png;base64,iVBOR",
            current_markdown="",
        )
        system = msgs[0]["content"]
        assert "# A" in system
        assert "## A.1" in system
        assert "### A.1.1" in system

    def test_custom_prompt_overrides_default(self):
        from atlas.ingest.page_renderer import build_vision_messages
        custom = "Just extract the text."
        msgs = build_vision_messages(
            page_image_uri="data:image/png;base64,iVBOR",
            current_markdown="",
            system_prompt=custom,
        )
        system = msgs[0]["content"]
        assert system == custom
        assert "Heading formatting" not in system
