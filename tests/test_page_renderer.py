"""Tests for page_renderer.py — PDF page rendering for VLM workflows."""
from __future__ import annotations

import base64
import struct
import zlib
from typing import Any

import pytest

from atlas.ingest.page_renderer import (
    CropMargins,
    build_vision_messages,
    page_count,
    render_page,
    render_page_base64,
)

# ---------------------------------------------------------------------------
# Minimal valid PDF — one blank A4 page (~300 bytes)
# ---------------------------------------------------------------------------

_MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    b"3 0 obj\n<< /Type /Page /Parent 2 0 R "
    b"/MediaBox [0 0 612 792] /Contents 4 0 R /Resources << >> >>\nendobj\n"
    b"4 0 obj\n<< /Length 0 >>\nstream\n\nendstream\nendobj\n"
    b"xref\n0 5\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000058 00000 n \n"
    b"0000000115 00000 n \n"
    b"0000000236 00000 n \n"
    b"trailer\n<< /Root 1 0 R /Size 5 >>\n"
    b"startxref\n289\n%%EOF\n"
)


def _make_two_page_pdf() -> bytes:
    """Create a minimal 2-page PDF."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R "
        b"/MediaBox [0 0 612 792] /Contents 4 0 R /Resources << >> >>\nendobj\n"
        b"4 0 obj\n<< /Length 0 >>\nstream\n\nendstream\nendobj\n"
        b"5 0 obj\n<< /Type /Page /Parent 2 0 R "
        b"/MediaBox [0 0 612 792] /Contents 6 0 R /Resources << >> >>\nendobj\n"
        b"6 0 obj\n<< /Length 0 >>\nstream\n\nendstream\nendobj\n"
        b"xref\n0 7\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000123 00000 n \n"
        b"0000000244 00000 n \n"
        b"0000000297 00000 n \n"
        b"0000000418 00000 n \n"
        b"trailer\n<< /Root 1 0 R /Size 7 >>\n"
        b"startxref\n471\n%%EOF\n"
    )


# ===================================================================
# CropMargins
# ===================================================================


class TestCropMargins:
    def test_defaults(self) -> None:
        c = CropMargins()
        assert c.top == 0.0
        assert c.bottom == 0.0
        assert c.left == 0.0
        assert c.right == 0.0

    def test_valid_values(self) -> None:
        c = CropMargins(top=0.1, bottom=0.2, left=0.05, right=0.05)
        assert c.top == 0.1
        assert c.bottom == 0.2

    def test_rejects_negative(self) -> None:
        with pytest.raises(ValueError, match="top"):
            CropMargins(top=-0.1)

    def test_rejects_too_large(self) -> None:
        with pytest.raises(ValueError, match="bottom"):
            CropMargins(bottom=0.5)

    def test_edge_zero(self) -> None:
        c = CropMargins(top=0.0, bottom=0.0)
        assert c.top == 0.0

    def test_just_under_half(self) -> None:
        c = CropMargins(top=0.49)
        assert c.top == 0.49


# ===================================================================
# page_count
# ===================================================================


class TestPageCount:
    def test_single_page(self) -> None:
        assert page_count(_MINIMAL_PDF) == 1

    def test_two_pages(self) -> None:
        assert page_count(_make_two_page_pdf()) == 2

    def test_invalid_pdf(self) -> None:
        with pytest.raises(Exception):
            page_count(b"not a pdf")


# ===================================================================
# render_page
# ===================================================================


class TestRenderPage:
    def test_renders_png(self) -> None:
        png = render_page(_MINIMAL_PDF, 0, dpi=72)
        # PNG magic bytes
        assert png[:4] == b"\x89PNG"

    def test_higher_dpi_larger(self) -> None:
        png72 = render_page(_MINIMAL_PDF, 0, dpi=72)
        png150 = render_page(_MINIMAL_PDF, 0, dpi=150)
        assert len(png150) > len(png72)

    def test_out_of_range_page(self) -> None:
        with pytest.raises(IndexError, match="out of range"):
            render_page(_MINIMAL_PDF, 5, dpi=72)

    def test_negative_page(self) -> None:
        with pytest.raises(IndexError):
            render_page(_MINIMAL_PDF, -1, dpi=72)

    def test_with_crop(self) -> None:
        crop = CropMargins(top=0.1, bottom=0.1)
        png = render_page(_MINIMAL_PDF, 0, dpi=72, crop=crop)
        assert png[:4] == b"\x89PNG"

    def test_no_crop(self) -> None:
        png = render_page(_MINIMAL_PDF, 0, dpi=72, crop=None)
        assert png[:4] == b"\x89PNG"

    def test_second_page(self) -> None:
        pdf = _make_two_page_pdf()
        png = render_page(pdf, 1, dpi=72)
        assert png[:4] == b"\x89PNG"


# ===================================================================
# render_page_base64
# ===================================================================


class TestRenderPageBase64:
    def test_returns_data_uri(self) -> None:
        uri = render_page_base64(_MINIMAL_PDF, 0, dpi=72)
        assert uri.startswith("data:image/png;base64,")

    def test_decodable(self) -> None:
        uri = render_page_base64(_MINIMAL_PDF, 0, dpi=72)
        b64 = uri.split(",", 1)[1]
        decoded = base64.b64decode(b64)
        assert decoded[:4] == b"\x89PNG"


# ===================================================================
# build_vision_messages
# ===================================================================


class TestBuildVisionMessages:
    def test_default_prompt(self) -> None:
        msgs = build_vision_messages(
            page_image_uri="data:image/png;base64,abc",
            current_markdown="# Hello",
        )
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert "document-processing" in msgs[0]["content"]
        assert msgs[1]["role"] == "user"

    def test_user_message_is_multimodal(self) -> None:
        msgs = build_vision_messages(
            page_image_uri="data:image/png;base64,abc",
            current_markdown="# Test",
        )
        content = msgs[1]["content"]
        assert isinstance(content, list)
        assert len(content) == 2
        assert content[0]["type"] == "image_url"
        assert content[1]["type"] == "text"

    def test_custom_prompt(self) -> None:
        msgs = build_vision_messages(
            page_image_uri="data:image/png;base64,abc",
            current_markdown="test",
            system_prompt="Custom system prompt.",
        )
        assert msgs[0]["content"] == "Custom system prompt."

    def test_image_url_detail(self) -> None:
        msgs = build_vision_messages(
            page_image_uri="data:image/png;base64,xyz",
            current_markdown="md",
        )
        img_block = msgs[1]["content"][0]
        assert img_block["image_url"]["detail"] == "high"
        assert img_block["image_url"]["url"] == "data:image/png;base64,xyz"

    def test_markdown_included_in_text(self) -> None:
        msgs = build_vision_messages(
            page_image_uri="data:image/png;base64,abc",
            current_markdown="## Section\nContent here.",
        )
        text_block = msgs[1]["content"][1]
        assert "## Section" in text_block["text"]
        assert "Content here." in text_block["text"]

    def test_default_prompt_heading_hierarchy(self) -> None:
        msgs = build_vision_messages(
            page_image_uri="data:image/png;base64,abc",
            current_markdown="",
        )
        system = msgs[0]["content"]
        # Numbered section hierarchy
        assert "# 1" in system
        assert "## 1.1" in system
        assert "### 1.1.1" in system
        # Appendix hierarchy
        assert "# A" in system
        assert "## A.1" in system
        assert "### A.1.1" in system
        # General heading rules
        assert "heading" in system.lower()
