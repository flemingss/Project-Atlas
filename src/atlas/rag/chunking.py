from __future__ import annotations

import re
from dataclasses import dataclass, field


def _approx_tokens(text: str) -> int:
    # Cheap approximation good enough for chunk sizing without tokenizer deps.
    # Roughly 4 chars/token for English-ish text.
    t = (text or "").strip()
    if not t:
        return 0
    return max(1, len(t) // 4)


def _is_heading(line: str) -> tuple[int, str] | None:
    m = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
    if not m:
        return None
    return len(m.group(1)), m.group(2).strip()


def _looks_like_table_line(line: str) -> bool:
    s = line.strip()
    if "|" not in s:
        return False
    # Avoid treating inline pipes as tables.
    return s.startswith("|") or s.endswith("|")


def _looks_like_table_sep(line: str) -> bool:
    s = line.strip()
    if "|" not in s:
        return False
    # Basic markdown table separator: | --- | :---: |
    parts = [p.strip() for p in s.strip("|").split("|")]
    if not parts:
        return False
    for p in parts:
        if not p:
            continue
        if not re.fullmatch(r":?-{3,}:?", p):
            return False
    return True


def _is_list_item(line: str) -> bool:
    s = line.lstrip()
    return bool(re.match(r"^(?:[-*+]\s+|\d+\.\s+)", s))


def _is_indented_continuation(line: str) -> bool:
    # Continuation line for a list item.
    return bool(re.match(r"^\s{2,}\S+", line))


@dataclass(frozen=True)
class ChunkFeatures:
    has_table: bool
    is_procedure: bool
    has_code: bool


def infer_chunk_features(text: str) -> ChunkFeatures:
    t = text or ""
    has_code = "```" in t
    has_table = any(_looks_like_table_line(ln) for ln in t.split("\n")) and any(_looks_like_table_sep(ln) for ln in t.split("\n"))
    # Procedure heuristic: numbered list items are present.
    is_procedure = bool(re.search(r"^\s*\d+\.\s+", t, flags=re.MULTILINE))
    return ChunkFeatures(has_table=bool(has_table), is_procedure=bool(is_procedure), has_code=bool(has_code))


def chunk_markdown_semantic(
    *,
    text: str,
    target_tokens: int = 320,
    max_tokens: int = 400,
) -> list[TextChunk]:
    """Structure-aware chunking for Markdown.

    - Prefers splitting by headings + paragraphs
    - Never splits inside a code fence
    - Keeps markdown tables as atomic blocks
    - Keeps list/procedure blocks together
    - Tracks section_path based on heading stack
    """
    cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not cleaned:
        return []

    lines = cleaned.split("\n")

    # Convert to blocks: heading, code, table, list, paragraph.
    blocks: list[tuple[str, str, list[str]]] = []  # (kind, raw_text, section_path)

    heading_stack: list[tuple[int, str]] = []
    section_path: list[str] = []

    i = 0
    in_code = False
    code_buf: list[str] = []
    para_buf: list[str] = []

    def flush_para() -> None:
        nonlocal para_buf
        if not para_buf:
            return
        raw = "\n".join(para_buf).strip("\n")
        if raw.strip():
            blocks.append(("paragraph", raw, section_path.copy()))
        para_buf = []

    while i < len(lines):
        ln = lines[i]

        # Code fence blocks.
        if ln.strip().startswith("```"):
            if in_code:
                code_buf.append(ln)
                blocks.append(("code", "\n".join(code_buf), section_path.copy()))
                code_buf = []
                in_code = False
                i += 1
                continue

            flush_para()
            in_code = True
            code_buf = [ln]
            i += 1
            continue

        if in_code:
            code_buf.append(ln)
            i += 1
            continue

        # Heading.
        h = _is_heading(ln)
        if h is not None:
            flush_para()
            level, title = h
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            section_path = [x[1] for x in heading_stack]
            blocks.append(("heading", ln.strip(), section_path.copy()))
            i += 1
            continue

        # Table: require header row + separator line.
        if _looks_like_table_line(ln) and (i + 1) < len(lines) and _looks_like_table_sep(lines[i + 1]):
            flush_para()
            tbuf = [ln, lines[i + 1]]
            i += 2
            while i < len(lines) and _looks_like_table_line(lines[i]):
                tbuf.append(lines[i])
                i += 1
            blocks.append(("table", "\n".join(tbuf), section_path.copy()))
            continue

        # List/procedure blocks.
        if _is_list_item(ln):
            flush_para()
            lbuf = [ln]
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() == "":
                    # Keep a single blank line inside list block if followed by continuation.
                    # We'll stop list block if next non-blank is not list/continuation.
                    j = i + 1
                    while j < len(lines) and lines[j].strip() == "":
                        j += 1
                    if j < len(lines) and (_is_list_item(lines[j]) or _is_indented_continuation(lines[j])):
                        lbuf.append("")
                        i = j
                        continue
                    break
                if _is_list_item(nxt) or _is_indented_continuation(nxt):
                    lbuf.append(nxt)
                    i += 1
                    continue
                break
            blocks.append(("list", "\n".join(lbuf).strip("\n"), section_path.copy()))
            continue

        # Paragraph accumulation (including single lines).
        if ln.strip() == "":
            flush_para()
            i += 1
            continue
        para_buf.append(ln)
        i += 1

    flush_para()
    if in_code and code_buf:
        blocks.append(("code", "\n".join(code_buf), section_path.copy()))

    # Assemble blocks into chunks.
    chunks: list[TextChunk] = []
    current_parts: list[str] = []
    current_tokens = 0
    current_path: list[str] = []

    def flush_chunk() -> None:
        nonlocal current_parts, current_tokens, current_path
        if not current_parts:
            return
        txt = "\n\n".join(p for p in current_parts if p is not None).strip()
        if txt:
            chunks.append(TextChunk(index=len(chunks), text=txt, section_path=current_path.copy()))
        current_parts = []
        current_tokens = 0
        current_path = []

    for kind, raw, path in blocks:
        block_text = raw.strip("\n")
        btoks = _approx_tokens(block_text)

        # Headings are context carriers; allow them to start new chunks.
        if kind == "heading":
            flush_chunk()
            current_parts = [block_text]
            current_tokens = btoks
            current_path = path
            continue

        # If path changes (new section), flush.
        if current_parts and current_path != path and path:
            flush_chunk()

        if not current_parts:
            current_parts = [block_text]
            current_tokens = btoks
            current_path = path
            continue

        # Add block if it fits, else flush and start new chunk.
        if current_tokens + btoks <= max_tokens or current_tokens < target_tokens:
            current_parts.append(block_text)
            current_tokens += btoks
            if not current_path:
                current_path = path
        else:
            flush_chunk()
            current_parts = [block_text]
            current_tokens = btoks
            current_path = path

    flush_chunk()
    return chunks


@dataclass
class TextChunk:
    index: int
    text: str
    parent_header_id: str | None = None
    sibling_ids: list[str] = field(default_factory=list)
    section_path: list[str] = field(default_factory=list)


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

    # Update sibling indices (note: these are indices, not UUIDs)
    # In a full implementation, these would be replaced with deterministic chunk IDs
    for parent_id, sibling_indices in section_siblings.items():
        for idx in sibling_indices:
            # Store sibling indices for now; integration with deterministic IDs
            # happens during the commit phase when UUIDs are generated
            chunk = chunks[idx]
            chunk.sibling_ids = [str(i) for i in sibling_indices if i != idx]

    return chunks
