"""Tests for atlas.vlm_ingest.session — session model + registry."""
from __future__ import annotations

import time

import pytest

from atlas.vlm_ingest.session import (
    PageStatus,
    SessionStatus,
    PageSettings,
    VlmIngestConfig,
    VlmIngestSession,
    SessionRegistry,
)
from atlas.vlm_ingest.stitcher import PageResult


# ---------------------------------------------------------------------------
# VlmIngestConfig
# ---------------------------------------------------------------------------


class TestVlmIngestConfig:
    def test_default_settings(self):
        cfg = VlmIngestConfig()
        assert cfg.dpi == 200
        assert cfg.crop_top == 0.04

    def test_settings_for_page_defaults(self):
        cfg = VlmIngestConfig(dpi=300)
        ps = cfg.settings_for_page(0)
        assert ps.dpi == 300
        assert ps.crop_top == 0.04
        assert ps.enabled is True

    def test_settings_for_page_override(self):
        cfg = VlmIngestConfig(dpi=200, page_overrides={2: {"dpi": 400, "crop_top": 0.1}})
        ps_default = cfg.settings_for_page(0)
        assert ps_default.dpi == 200

        ps_override = cfg.settings_for_page(2)
        assert ps_override.dpi == 400
        assert ps_override.crop_top == 0.1

    def test_roundtrip_dict(self):
        cfg = VlmIngestConfig(dpi=300, system_prompt="test", page_overrides={1: {"dpi": 150}})
        d = cfg.to_dict()
        cfg2 = VlmIngestConfig.from_dict(d)
        assert cfg2.dpi == 300
        assert cfg2.system_prompt == "test"
        assert cfg2.page_overrides == {1: {"dpi": 150}}


# ---------------------------------------------------------------------------
# VlmIngestSession
# ---------------------------------------------------------------------------


class TestVlmIngestSession:
    def _make_session(self, pages: int = 5) -> VlmIngestSession:
        return VlmIngestSession(
            session_id="test123",
            pdf_bytes=b"fake-pdf",
            page_count=pages,
            source_filename="test.pdf",
        )

    def test_init_all_pages_pending(self):
        s = self._make_session(3)
        assert len(s.page_statuses) == 3
        assert all(v == PageStatus.PENDING for v in s.page_statuses.values())

    def test_all_pages_enabled_by_default(self):
        s = self._make_session(3)
        assert s.enabled_pages() == [0, 1, 2]

    def test_disable_page(self):
        s = self._make_session(3)
        s.update_page_settings(1, enabled=False)
        assert 1 not in s.enabled_pages()

    def test_next_pending_page(self):
        s = self._make_session(3)
        assert s.next_pending_page() == 0
        s.set_page_result(0, PageResult(page_num=0, markdown="p0"))
        assert s.next_pending_page() == 1

    def test_skip_page(self):
        s = self._make_session(3)
        s.skip_page(0)
        assert s.page_statuses[0] == PageStatus.SKIPPED
        assert s.next_pending_page() == 1

    def test_set_page_error(self):
        s = self._make_session(2)
        s.set_page_error(0, "VLM timeout")
        assert s.page_statuses[0] == PageStatus.ERROR
        assert s.page_errors[0] == "VLM timeout"

    def test_all_done(self):
        s = self._make_session(2)
        assert not s.all_done()
        s.set_page_result(0, PageResult(page_num=0, markdown="a"))
        assert not s.all_done()
        s.set_page_result(1, PageResult(page_num=1, markdown="b"))
        assert s.all_done()

    def test_all_done_with_skip(self):
        s = self._make_session(2)
        s.set_page_result(0, PageResult(page_num=0, markdown="a"))
        s.skip_page(1)
        assert s.all_done()

    def test_progress(self):
        s = self._make_session(3)
        s.set_page_result(0, PageResult(page_num=0, markdown="a"))
        s.skip_page(1)
        prog = s.progress()
        assert prog["done"] == 1
        assert prog["skipped"] == 1
        assert prog["pending"] == 1
        assert prog["total"] == 3
        assert prog["enabled"] == 3

    def test_stitch(self):
        s = self._make_session(2)
        s.set_page_result(0, PageResult(page_num=0, markdown="Page 0"))
        s.set_page_result(1, PageResult(page_num=1, markdown="Page 1"))
        result = s.stitch()
        assert "Page 0" in result.markdown
        assert "Page 1" in result.markdown
        assert s.status == SessionStatus.COMPLETE

    def test_stitch_skipped_pages_excluded(self):
        s = self._make_session(3)
        s.set_page_result(0, PageResult(page_num=0, markdown="First"))
        s.skip_page(1)
        s.set_page_result(2, PageResult(page_num=2, markdown="Third"))
        result = s.stitch()
        assert "First" in result.markdown
        assert "Third" in result.markdown
        assert result.pages_processed == 2

    def test_summary(self):
        s = self._make_session(2)
        summary = s.summary()
        assert summary["session_id"] == "test123"
        assert summary["page_count"] == 2
        assert "progress" in summary
        assert "config" in summary

    def test_update_page_settings_creates_override(self):
        s = self._make_session(3)
        s.update_page_settings(1, dpi=400, crop_top=0.1)
        ps = s.config.settings_for_page(1)
        assert ps.dpi == 400
        assert ps.crop_top == 0.1


# ---------------------------------------------------------------------------
# SessionRegistry
# ---------------------------------------------------------------------------


class TestSessionRegistry:
    def test_create_and_get(self):
        reg = SessionRegistry()
        s = reg.create(pdf_bytes=b"pdf", page_count=3)
        assert reg.get(s.session_id) is s

    def test_delete(self):
        reg = SessionRegistry()
        s = reg.create(pdf_bytes=b"pdf", page_count=1)
        assert reg.delete(s.session_id)
        assert reg.get(s.session_id) is None

    def test_delete_nonexistent(self):
        reg = SessionRegistry()
        assert not reg.delete("nope")

    def test_list_sessions(self):
        reg = SessionRegistry()
        reg.create(pdf_bytes=b"pdf1", page_count=1, source_filename="a.pdf")
        reg.create(pdf_bytes=b"pdf2", page_count=2, source_filename="b.pdf")
        listing = reg.list_sessions()
        assert len(listing) == 2
        assert {s["source_filename"] for s in listing} == {"a.pdf", "b.pdf"}

    def test_max_sessions_limit(self):
        reg = SessionRegistry(max_sessions=2)
        reg.create(pdf_bytes=b"1", page_count=1)
        reg.create(pdf_bytes=b"2", page_count=1)
        with pytest.raises(RuntimeError, match="Session limit"):
            reg.create(pdf_bytes=b"3", page_count=1)

    def test_ttl_eviction(self):
        reg = SessionRegistry(ttl_seconds=0.01)
        s = reg.create(pdf_bytes=b"pdf", page_count=1)
        time.sleep(0.05)
        # Next create triggers eviction
        s2 = reg.create(pdf_bytes=b"pdf2", page_count=1)
        assert reg.get(s.session_id) is None
        assert reg.get(s2.session_id) is s2

    def test_create_with_config(self):
        reg = SessionRegistry()
        cfg = VlmIngestConfig(dpi=300, crop_top=0.1)
        s = reg.create(pdf_bytes=b"pdf", page_count=5, config=cfg)
        assert s.config.dpi == 300

    def test_create_headless(self):
        reg = SessionRegistry()
        s = reg.create(pdf_bytes=b"pdf", page_count=2, headless=True)
        assert s.headless is True
