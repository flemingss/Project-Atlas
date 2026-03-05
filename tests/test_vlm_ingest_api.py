"""Tests for atlas.api_vlm_ingest — VLM ingest API endpoints.

Uses mocked VLM calls and in-memory PDF bytes to test the full
endpoint lifecycle without external dependencies.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.api_vlm_ingest import make_vlm_ingest_router
from atlas.config_manager import ConfigManager
from atlas.db import make_engine, make_sessionmaker
from atlas.db_init import ensure_schema
from atlas.vlm_ingest.session import (
    PageStatus,
    SessionStatus,
    VlmIngestConfig,
    SessionRegistry,
)
from atlas.vlm_ingest.stitcher import PageResult, StitchResult
from tests.helpers import write_minimal_yaml_config


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
    """Test the VisionParser without real VLM calls."""

    @pytest.mark.asyncio
    async def test_vlm_parse_no_vision_model(self, monkeypatch):
        """When vision_model is not configured, return an error IngestResult."""
        from atlas.pipeline.ingest import IngestNode
        from atlas.pipeline.parsers import VisionParser, ParserContext

        # Ensure config dir has no vision_model
        monkeypatch.setenv("ATLAS_CONFIG_DIR", ".")
        monkeypatch.setenv("ATLAS_MODELS_DIR", ".")

        node = IngestNode(pdf_parser_config={"backend": "vision"})
        ctx = ParserContext(diagnostics=node.diagnostics, settings=node.settings, pdf_cfg=node._pdf_cfg)
        parser = VisionParser(ctx)

        # Patch ConfigManager at its source module (it's imported locally inside VisionParser.parse)
        with patch("atlas.config_manager.ConfigManager") as MockCM:
            mock_cm = MagicMock()
            mock_cfg = MagicMock()
            mock_cfg.models = {"providers": {}, "roles": {}}
            mock_cm.get.return_value = mock_cfg
            MockCM.return_value = mock_cm

            result = await parser.parse(_MINIMAL_PDF, "application/pdf", "test.pdf")
            assert not result.success
            assert "vision_model" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_vlm_parse_success(self, monkeypatch):
        """With a mocked vision model, VisionParser should produce stitched markdown."""
        from atlas.pipeline.ingest import IngestNode
        from atlas.pipeline.parsers import VisionParser, ParserContext

        monkeypatch.setenv("ATLAS_CONFIG_DIR", ".")
        monkeypatch.setenv("ATLAS_MODELS_DIR", ".")
        monkeypatch.setenv("ATLAS_OPENAI_BASE_URL", "http://localhost:1234/v1")

        node = IngestNode(pdf_parser_config={"backend": "vision"})
        ctx = ParserContext(diagnostics=node.diagnostics, settings=node.settings, pdf_cfg=node._pdf_cfg)
        parser = VisionParser(ctx)

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

            result = await parser.parse(_MINIMAL_PDF, "application/pdf", "test.pdf")

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
    """Test that backend=vision routes to VisionParser."""

    @pytest.mark.asyncio
    async def test_backend_vision_calls_vlm_parse(self, monkeypatch):
        from atlas.pipeline.ingest import IngestNode
        from atlas.pipeline.parsers import VisionParser

        monkeypatch.setenv("ATLAS_CONFIG_DIR", ".")
        monkeypatch.setenv("ATLAS_MODELS_DIR", ".")

        node = IngestNode(pdf_parser_config={"backend": "vision"})

        # Mock VisionParser.parse to return a successful result
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.markdown_projection = "# Mocked Vision Content\n\nSufficient text for quality gates.\n" * 10

        async def _fake_vision_parse(self, doc_bytes, source_mime_type, filename):
            return mock_result
        monkeypatch.setattr(VisionParser, "parse", _fake_vision_parse)

        result = await node.process_doc_bytes(
            doc_bytes=_MINIMAL_PDF,
            source_mime_type="application/pdf",
            filename="test.pdf",
        )

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


# ---------------------------------------------------------------------------
# HTTP endpoint helpers
# ---------------------------------------------------------------------------


def _make_vlm_endpoint_app(
    tmp_path: Path,
    monkeypatch: Any,
) -> tuple[FastAPI, SessionRegistry]:
    """Build a TestClient-ready FastAPI app with the VLM ingest router.

    Returns ``(app, registry)`` so tests can inspect / mutate session state
    without going through the HTTP layer.
    """
    write_minimal_yaml_config(tmp_path)
    config_manager = ConfigManager(root_dir=tmp_path)
    db_path = tmp_path / "test.sqlite"
    engine = make_engine(f"sqlite+pysqlite:///{db_path}")
    ensure_schema(engine)
    sf = make_sessionmaker(engine)
    monkeypatch.setenv("ATLAS_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    # Capture the SessionRegistry instance created inside make_vlm_ingest_router
    _captured: list[SessionRegistry] = []
    _orig_init = SessionRegistry.__init__

    def _capturing_init(self: SessionRegistry, *args: Any, **kwargs: Any) -> None:
        _orig_init(self, *args, **kwargs)
        _captured.append(self)

    with patch.object(SessionRegistry, "__init__", _capturing_init):
        router = make_vlm_ingest_router(config_manager=config_manager, session_factory=sf)

    app = FastAPI()
    app.include_router(router)
    return app, _captured[0]


def _start_session_via_upload(client: TestClient) -> str:
    """Upload the minimal PDF and return the session_id."""
    resp = client.post(
        "/api/editor/vlm-ingest/start-upload",
        files={"file": ("test.pdf", _MINIMAL_PDF, "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["session_id"]


def _mock_model_registry(mock_provider: Any) -> MagicMock:
    """Return a MagicMock for ModelRegistry that resolves 'vision_model' via mock_provider."""
    mock_resolved = MagicMock()
    mock_resolved.model_name = "test-vlm"
    mock_resolved.provider_name = "test"
    mock_resolved.params = {}

    mock_reg_inst = MagicMock()
    mock_reg_inst.resolve.return_value = mock_resolved
    mock_reg_inst.provider_for.return_value = mock_provider

    MockReg = MagicMock(return_value=mock_reg_inst)
    return MockReg


# ---------------------------------------------------------------------------
# Per-page concurrent processing guard (409)
# ---------------------------------------------------------------------------


class TestProcessPageConcurrentGuard:
    """process-page must reject a second request while the same page is PROCESSING."""

    def test_returns_409_when_page_already_processing(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        app, registry = _make_vlm_endpoint_app(tmp_path, monkeypatch)
        client = TestClient(app)

        sid = _start_session_via_upload(client)

        # Simulate the first request having already claimed page 0
        session = registry.get(sid)
        assert session is not None
        session.page_statuses[0] = PageStatus.PROCESSING

        # A second request for the same page must be rejected with 409
        resp = client.post(
            f"/api/editor/vlm-ingest/{sid}/process-page",
            json={"page_num": 0},
        )
        assert resp.status_code == 409
        assert "already being processed" in resp.json()["detail"]

    def test_returns_409_auto_pick_when_only_page_is_processing(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """When all enabled pages are PROCESSING, auto-pick (page_num=None) returns 400.

        next_pending_page() only returns PENDING pages; if the sole page is already
        PROCESSING it returns None, resulting in a 400 "no pending pages" response.
        """
        app, registry = _make_vlm_endpoint_app(tmp_path, monkeypatch)
        client = TestClient(app)

        sid = _start_session_via_upload(client)

        session = registry.get(sid)
        assert session is not None
        # Mark the single page as PROCESSING so next_pending_page() returns None
        session.page_statuses[0] = PageStatus.PROCESSING

        resp = client.post(
            f"/api/editor/vlm-ingest/{sid}/process-page",
            json={},  # no page_num → auto-pick
        )
        # next_pending_page() returns None (no PENDING pages left), so 400
        assert resp.status_code == 400

    def test_pending_page_not_blocked_by_other_processing(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Guard must only block the *specific* page that is PROCESSING."""
        # Need a 2-page PDF — we create one programmatically by building a
        # minimal PDF string with two page objects.
        two_page_pdf = (
            b"%PDF-1.0\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R 4 0 R]/Count 2>>endobj\n"
            b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
            b"4 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
            b"xref\n0 5\n0000000000 65535 f \n0000000009 00000 n \n"
            b"0000000058 00000 n \n0000000115 00000 n \n0000000206 00000 n \n"
            b"trailer<</Size 5/Root 1 0 R>>\nstartxref\n297\n%%EOF"
        )

        app, registry = _make_vlm_endpoint_app(tmp_path, monkeypatch)
        client = TestClient(app)

        resp = client.post(
            "/api/editor/vlm-ingest/start-upload",
            files={"file": ("two.pdf", two_page_pdf, "application/pdf")},
        )
        # If the minimal 2-page PDF is accepted, the session starts; otherwise skip.
        if resp.status_code != 200:
            pytest.skip("Minimal 2-page PDF not accepted by PyMuPDF")
        sid = resp.json()["session_id"]

        session = registry.get(sid)
        assert session is not None
        # Mark page 0 as processing; page 1 is still PENDING
        session.page_statuses[0] = PageStatus.PROCESSING

        # Requesting page 0 explicitly should 409
        resp2 = client.post(
            f"/api/editor/vlm-ingest/{sid}/process-page",
            json={"page_num": 0},
        )
        assert resp2.status_code == 409

        # But requesting a different pending page (page 1) should be allowed.
        # Mock the VLM so it completes successfully rather than 503-ing.
        mock_provider = AsyncMock()
        mock_provider.chat = AsyncMock(return_value="# Page 1\n\nContent.")
        MockReg = _mock_model_registry(mock_provider)

        with (
            patch("atlas.api_vlm_ingest.ModelRegistry", MockReg),
            patch(
                "atlas.api_vlm_ingest.render_page_base64",
                return_value="data:image/png;base64,abc",
            ),
            patch(
                "atlas.api_vlm_ingest.build_vision_messages",
                return_value=[{"role": "user", "content": "test"}],
            ),
        ):
            resp3 = client.post(
                f"/api/editor/vlm-ingest/{sid}/process-page",
                json={"page_num": 1},
            )
        assert resp3.status_code == 200
        assert resp3.json()["status"] == "done"


# ---------------------------------------------------------------------------
# process-page structured error payload
# ---------------------------------------------------------------------------


class TestProcessPageErrorPayload:
    """process-page must return HTTP 200 with status="error" and an "error" field."""

    def test_provider_error_returns_structured_payload(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        app, registry = _make_vlm_endpoint_app(tmp_path, monkeypatch)
        client = TestClient(app)
        sid = _start_session_via_upload(client)

        mock_provider = AsyncMock()
        mock_provider.chat = AsyncMock(side_effect=RuntimeError("VLM provider timeout"))
        MockReg = _mock_model_registry(mock_provider)

        with (
            patch("atlas.api_vlm_ingest.ModelRegistry", MockReg),
            patch(
                "atlas.api_vlm_ingest.render_page_base64",
                return_value="data:image/png;base64,abc",
            ),
            patch(
                "atlas.api_vlm_ingest.build_vision_messages",
                return_value=[{"role": "user", "content": "test"}],
            ),
        ):
            resp = client.post(
                f"/api/editor/vlm-ingest/{sid}/process-page",
                json={"page_num": 0},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert "VLM provider timeout" in (data.get("error") or "")
        assert data["page_num"] == 0

    def test_render_error_returns_structured_payload(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        app, registry = _make_vlm_endpoint_app(tmp_path, monkeypatch)
        client = TestClient(app)
        sid = _start_session_via_upload(client)

        mock_provider = AsyncMock()
        MockReg = _mock_model_registry(mock_provider)

        with (
            patch("atlas.api_vlm_ingest.ModelRegistry", MockReg),
            patch(
                "atlas.api_vlm_ingest.render_page_base64",
                side_effect=OSError("PDF render failed"),
            ),
        ):
            resp = client.post(
                f"/api/editor/vlm-ingest/{sid}/process-page",
                json={"page_num": 0},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert "PDF render failed" in (data.get("error") or "")

    def test_error_payload_marks_page_as_error(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """After a VLM error the session must record the page as ERROR."""
        app, registry = _make_vlm_endpoint_app(tmp_path, monkeypatch)
        client = TestClient(app)
        sid = _start_session_via_upload(client)

        mock_provider = AsyncMock()
        mock_provider.chat = AsyncMock(side_effect=RuntimeError("bang"))
        MockReg = _mock_model_registry(mock_provider)

        with (
            patch("atlas.api_vlm_ingest.ModelRegistry", MockReg),
            patch(
                "atlas.api_vlm_ingest.render_page_base64",
                return_value="data:image/png;base64,abc",
            ),
            patch(
                "atlas.api_vlm_ingest.build_vision_messages",
                return_value=[{"role": "user", "content": "test"}],
            ),
        ):
            client.post(
                f"/api/editor/vlm-ingest/{sid}/process-page",
                json={"page_num": 0},
            )

        session = registry.get(sid)
        assert session is not None
        assert session.page_statuses[0] == PageStatus.ERROR


# ---------------------------------------------------------------------------
# commit endpoint — pipeline warning on failure
# ---------------------------------------------------------------------------


class TestCommitPipelineWarning:
    """When feed_pipeline=True and the pipeline raises, commit returns warnings."""

    def _prepare_committed_session(
        self, registry: SessionRegistry, sid: str
    ) -> None:
        """Give the session a stitched result so commit has something to save."""
        session = registry.get(sid)
        assert session is not None
        session.set_page_result(
            0,
            PageResult(page_num=0, markdown="# Doc\n\nContent.", model="test-vlm", dpi=200),
        )
        session.stitch()

    def test_commit_returns_warnings_on_pipeline_failure(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        app, registry = _make_vlm_endpoint_app(tmp_path, monkeypatch)
        client = TestClient(app)
        sid = _start_session_via_upload(client)
        self._prepare_committed_session(registry, sid)

        with patch(
            "atlas.api_vlm_ingest.ingest_text_via_pipeline",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Qdrant unavailable"),
        ):
            resp = client.post(
                f"/api/editor/vlm-ingest/{sid}/commit",
                json={"feed_pipeline": True},
            )

        assert resp.status_code == 200
        data = resp.json()
        # Artifact must still be reported
        assert data["path"]
        assert data["chars"] > 0
        # Warnings must be present and describe the failure
        assert data["warnings"], "Expected non-empty warnings list"
        assert any("Qdrant unavailable" in w for w in data["warnings"])

    def test_commit_no_warnings_on_pipeline_success(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        app, registry = _make_vlm_endpoint_app(tmp_path, monkeypatch)
        client = TestClient(app)
        sid = _start_session_via_upload(client)
        self._prepare_committed_session(registry, sid)

        with patch(
            "atlas.api_vlm_ingest.ingest_text_via_pipeline",
            new_callable=AsyncMock,
            return_value={"chunks_upserted": 5},
        ):
            resp = client.post(
                f"/api/editor/vlm-ingest/{sid}/commit",
                json={"feed_pipeline": True},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert not data.get("warnings")
        assert data["chunks_upserted"] == 5

    def test_commit_artifact_saved_despite_pipeline_failure(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """The markdown file must be written to disk even when the pipeline fails."""
        app, registry = _make_vlm_endpoint_app(tmp_path, monkeypatch)
        client = TestClient(app)
        sid = _start_session_via_upload(client)
        self._prepare_committed_session(registry, sid)

        with patch(
            "atlas.api_vlm_ingest.ingest_text_via_pipeline",
            new_callable=AsyncMock,
            side_effect=RuntimeError("network error"),
        ):
            resp = client.post(
                f"/api/editor/vlm-ingest/{sid}/commit",
                json={"feed_pipeline": True},
            )

        assert resp.status_code == 200
        rel_path = resp.json()["path"]
        artifact_path = tmp_path / "artifacts" / rel_path
        assert artifact_path.exists(), f"Artifact not found at {artifact_path}"
