from __future__ import annotations

import re


_RE_PAGE_NUMBER = re.compile(
    r"^(?:page\s+)?\d+(?:\s*/\s*\d+|\s+of\s+\d+)?\s*$",
    flags=re.IGNORECASE,
)


def _collapse_blank_lines(text: str) -> str:
    # Normalize line endings first.
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    # Trim trailing spaces.
    t = "\n".join(line.rstrip() for line in t.split("\n"))
    # Collapse 3+ blank lines to 2.
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip() + ("\n" if t.strip() else "")


def strip_noise_markdown(md: str) -> str:
    """Best-effort noise stripping.

    Conservative by design: focuses on common page-number/footer/header artifacts
    without requiring page boundary metadata.
    """

    text = (md or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]

    # Remove standalone page number lines.
    cleaned: list[str] = []
    for ln in lines:
        if _RE_PAGE_NUMBER.match(ln.strip()):
            # Only drop if the line is isolated-ish (no other content).
            cleaned.append("")
            continue
        cleaned.append(ln)

    # Remove extremely repetitive short lines (common headers/footers/watermarks).
    # Heuristic: if a non-empty line <= 80 chars appears >= 4 times, drop it.
    freq: dict[str, int] = {}
    for ln in cleaned:
        s = ln.strip()
        if not s:
            continue
        if len(s) > 80:
            continue
        freq[s] = freq.get(s, 0) + 1

    repetitive = {s for s, c in freq.items() if c >= 4}
    if repetitive:
        cleaned = ["" if ln.strip() in repetitive else ln for ln in cleaned]

    return _collapse_blank_lines("\n".join(cleaned))


def normalize_markdown(md: str) -> str:
    """Normalize Markdown for RAG indexing.

    Goals:
      - strip obvious noise
      - enforce spacing around headings/tables/lists enough to help chunking
      - keep transformation deterministic and low-risk
    """

    text = strip_noise_markdown(md)

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
