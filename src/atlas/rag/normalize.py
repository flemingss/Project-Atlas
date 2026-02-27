"""Lightweight markdown normalization for RAG indexing.

Goals:
  - enforce spacing around headings (helps chunking boundary detection)
  - normalize list numbering styles
  - collapse excessive blank lines

All transforms are pure formatting — no content is removed or modified.
Content-level noise stripping (page numbers, repetitive lines) is
handled by ``atlas.pipeline.cleanup`` builtin toggles.
"""

from __future__ import annotations

import re


def _collapse_blank_lines(text: str) -> str:
    """Collapse 3+ consecutive blank lines into exactly 2."""
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip() + ("\n" if t.strip() else "")


def normalize_markdown(md: str) -> str:
    """Normalize Markdown for RAG indexing.

    Pure formatting transforms — no content is removed.

    1. Collapse excessive blank lines.
    2. Ensure a blank line after headings (aids chunking).
    3. Normalize common list numbering style (``1)`` → ``1.``).
    """

    text = _collapse_blank_lines(md)

    # Ensure a blank line after headings (unless EOF).
    out_lines: list[str] = []
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        out_lines.append(ln)
        if re.match(r"^#{1,6}\s+\S+", ln):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if nxt.strip() and nxt != "":
                out_lines.append("")

    text = "\n".join(out_lines)

    # Normalize common list numbering style ("1)" -> "1.").
    text = re.sub(r"^(\s*)(\d+)\)\s+", r"\1\2. ", text, flags=re.MULTILINE)

    return _collapse_blank_lines(text)
