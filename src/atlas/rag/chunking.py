from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextChunk:
    index: int
    text: str


def chunk_text(*, text: str, max_chars: int) -> list[TextChunk]:
    cleaned = (text or "").strip()
    if not cleaned:
        return []

    paragraphs = [p.strip() for p in cleaned.split("\n\n") if p.strip()]
    chunks: list[TextChunk] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if not current:
            return
        chunks.append(TextChunk(index=len(chunks), text="\n\n".join(current).strip()))
        current = []
        current_len = 0

    for p in paragraphs:
        # Force-split oversized paragraphs.
        if len(p) > max_chars:
            flush()
            start = 0
            while start < len(p):
                part = p[start : start + max_chars].strip()
                if part:
                    chunks.append(TextChunk(index=len(chunks), text=part))
                start += max_chars
            continue

        if current_len and (current_len + 2 + len(p) > max_chars):
            flush()

        if current:
            current_len += 2  # account for joiner newlines
        current.append(p)
        current_len += len(p)

    flush()
    return chunks
