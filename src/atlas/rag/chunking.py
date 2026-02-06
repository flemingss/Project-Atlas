from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TextChunk:
    index: int
    text: str
    parent_header_id: str | None = None
    sibling_ids: list[str] = None
    section_path: list[str] = None

    def __post_init__(self):
        # Initialize mutable defaults
        if self.sibling_ids is None:
            object.__setattr__(self, "sibling_ids", [])
        if self.section_path is None:
            object.__setattr__(self, "section_path", [])


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


def chunk_text_hierarchical(
    *, text: str, max_chars: int
) -> list[TextChunk]:
    """Chunk text with heading-aware hierarchical structure (HLD section 2: Chunking).

    Features:
    - Heading-aware transformation
    - Store parent_header_id, sibling_ids
    - Track section_path (e.g., ["Chapter 1", "Thermal Dynamics"])

    This is an enhanced version that tracks document structure.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return []

    # Extract headings and build hierarchy
    lines = cleaned.split("\n")
    sections: list[dict] = []
    current_section: dict | None = None
    section_path: list[str] = []
    heading_stack: list[tuple[int, str]] = []  # (level, text)

    for line in lines:
        # Check for markdown heading
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)

        if heading_match:
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()

            # Update heading stack
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, heading_text))

            # Update section path
            section_path = [h[1] for h in heading_stack]

            # Close previous section
            if current_section:
                sections.append(current_section)

            # Start new section
            current_section = {
                "heading": heading_text,
                "level": level,
                "section_path": section_path.copy(),
                "content": [line],
                "parent_id": heading_stack[-2][1] if len(heading_stack) > 1 else None,
            }
        elif current_section:
            current_section["content"].append(line)
        else:
            # Content before first heading
            if not sections or not current_section:
                current_section = {
                    "heading": None,
                    "level": 0,
                    "section_path": [],
                    "content": [line],
                    "parent_id": None,
                }

    # Don't forget last section
    if current_section:
        sections.append(current_section)

    # Now chunk each section respecting max_chars
    chunks: list[TextChunk] = []
    section_siblings: dict[str, list[int]] = {}  # parent_id -> list of chunk indices

    for section in sections:
        section_text = "\n".join(section["content"])
        parent_id = section["parent_id"]

        # Split section into chunks if needed
        section_chunks = chunk_text(text=section_text, max_chars=max_chars)

        for sc in section_chunks:
            chunk_idx = len(chunks)

            # Track siblings
            if parent_id:
                if parent_id not in section_siblings:
                    section_siblings[parent_id] = []
                section_siblings[parent_id].append(chunk_idx)

            chunk = TextChunk(
                index=chunk_idx,
                text=sc.text,
                parent_header_id=parent_id,
                sibling_ids=[],  # Will be updated below
                section_path=section["section_path"],
            )
            chunks.append(chunk)

    # Update sibling IDs
    for parent_id, sibling_indices in section_siblings.items():
        for idx in sibling_indices:
            # Convert to string IDs
            sibling_strs = [str(i) for i in sibling_indices if i != idx]
            object.__setattr__(chunks[idx], "sibling_ids", sibling_strs)

    return chunks
