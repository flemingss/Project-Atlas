"""Render individual PDF pages to PNG images using PyMuPDF (fitz).

This module provides the plumbing for VLM (vision-language model) workflows:
a PDF page is rendered to a high-resolution PNG, optionally cropped to remove
header/footer regions, and returned as raw bytes suitable for base64 encoding
into an ``image_url`` content block for the OpenAI-compatible chat API.

Usage::

    from atlas.ingest.page_renderer import render_page, render_page_base64

    # Get raw PNG bytes
    png_bytes = render_page(pdf_bytes, page_num=0, dpi=200)

    # Get a data-URI ready for ChatMessage content parts
    data_uri = render_page_base64(pdf_bytes, page_num=0, dpi=200)

Dependencies:
    PyMuPDF >= 1.24.0 (already in pyproject.toml)
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Optional

import fitz  # PyMuPDF

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CropMargins:
    """Fractional crop margins (0.0–1.0) relative to page dimensions.

    For example, ``top=0.05`` removes the top 5% of the page (header area).
    """
    top: float = 0.0
    bottom: float = 0.0
    left: float = 0.0
    right: float = 0.0

    def __post_init__(self) -> None:
        for field_name in ("top", "bottom", "left", "right"):
            val = getattr(self, field_name)
            if not (0.0 <= val < 0.5):
                raise ValueError(f"CropMargins.{field_name} must be in [0.0, 0.5), got {val}")


# Sensible default: trim 4% top/bottom for typical header/footer areas
DEFAULT_CROP = CropMargins(top=0.04, bottom=0.04)


def page_count(pdf_bytes: bytes) -> int:
    """Return the number of pages in a PDF."""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return len(doc)


def render_page(
    pdf_bytes: bytes,
    page_num: int = 0,
    *,
    dpi: int = 200,
    crop: Optional[CropMargins] = None,
) -> bytes:
    """Render a single PDF page to PNG bytes.

    Args:
        pdf_bytes: Raw PDF file content.
        page_num: Zero-based page index.
        dpi: Render resolution (default 200 — good balance of VLM quality
             vs payload size; 150 for speed, 300 for max quality).
        crop: Optional fractional crop margins to remove headers/footers.
              Pass ``None`` to render the full page.

    Returns:
        PNG image bytes.

    Raises:
        IndexError: If page_num is out of range.
        RuntimeError: If PyMuPDF fails to render.
    """
    zoom = dpi / 72.0  # fitz uses 72 DPI as baseline
    mat = fitz.Matrix(zoom, zoom)

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        if page_num < 0 or page_num >= len(doc):
            raise IndexError(f"Page {page_num} out of range (PDF has {len(doc)} pages)")

        page = doc[page_num]

        if crop:
            # Compute crop rectangle in page coordinates
            rect = page.rect
            w, h = rect.width, rect.height
            clip = fitz.Rect(
                rect.x0 + w * crop.left,
                rect.y0 + h * crop.top,
                rect.x1 - w * crop.right,
                rect.y1 - h * crop.bottom,
            )
            pix = page.get_pixmap(matrix=mat, clip=clip)
        else:
            pix = page.get_pixmap(matrix=mat)

        png_bytes = pix.tobytes("png")

    log.debug(
        "Rendered page %d at %d DPI → %d bytes (crop=%s)",
        page_num, dpi, len(png_bytes), crop is not None,
    )
    return png_bytes


def render_page_base64(
    pdf_bytes: bytes,
    page_num: int = 0,
    *,
    dpi: int = 200,
    crop: Optional[CropMargins] = None,
) -> str:
    """Render a page and return a ``data:image/png;base64,...`` URI.

    This is ready to use in an OpenAI-compatible ``image_url`` content block::

        {"type": "image_url", "image_url": {"url": data_uri}}
    """
    png = render_page(pdf_bytes, page_num, dpi=dpi, crop=crop)
    b64 = base64.b64encode(png).decode("ascii")
    return f"data:image/png;base64,{b64}"


def build_vision_messages(
    *,
    page_image_uri: str,
    current_markdown: str,
    system_prompt: str | None = None,
) -> list[dict]:
    """Build multimodal message list for a VLM vision-refine call.

    Constructs a conversation with:
    1. System prompt (optional) explaining the task
    2. User message with the page image + current markdown for correction

    Returns a list of dicts ready for ``ChatMessage`` construction.
    """
    if system_prompt is None:
        system_prompt = (
            "You are a document-processing assistant. You receive a screenshot of ONE "
            "page from a PDF and the current markdown extraction of ONLY that page. "
            "Your task is to correct the markdown so it faithfully represents "
            "ONLY this single page's content.\n\n"
            "Rules:\n"
            "- Output ONLY the corrected markdown for THIS page — nothing more.\n"
            "- Preserve ALL content visible in the screenshot — do not summarize or omit anything.\n"
            "- Fix formatting errors: broken tables, garbled headings, OCR mistakes.\n"
            "- Use proper markdown: headings (#), lists (-/1.), tables (|), etc.\n"
            "- Do NOT add commentary, preamble, or postamble.\n"
            "- Do NOT include content from other pages.\n"
            "- Output ONLY the corrected markdown, no code fences.\n\n"
            "Heading formatting:\n"
            "- Reproduce the document's heading hierarchy using markdown heading levels.\n"
            "- Numbered sections: # 1  Title, ## 1.1  Subtitle, ### 1.1.1  Sub-subtitle, etc.\n"
            "- Appendix sections: # A  Title, ## A.1  Subtitle, ### A.1.1  Sub-subtitle, etc.\n"
            "- Always include the section number/letter as part of the heading text.\n"
            "- Match the depth of `#` marks to the nesting level visible in the document."
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": page_image_uri, "detail": "high"},
                },
                {
                    "type": "text",
                    "text": (
                        "Above is a screenshot of ONE page from a PDF document. "
                        "Below is the current markdown extraction for ONLY this page. "
                        "Please correct it to accurately match what you see in the "
                        "screenshot. Output only the corrected markdown for this "
                        "single page:\n\n"
                        f"```markdown\n{current_markdown}\n```"
                    ),
                },
            ],
        },
    ]
    return messages
