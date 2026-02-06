from __future__ import annotations

import uuid

from atlas.rag.chunking import chunk_text
from atlas.rag.deterministic import deterministic_chunk_id, sha256_hex


def test_chunk_text_respects_max_chars() -> None:
    text = "para1\n\n" + ("x" * 50) + "\n\npara3"
    chunks = chunk_text(text=text, max_chars=30)
    assert chunks
    assert all(len(c.text) <= 30 for c in chunks)


def test_deterministic_chunk_id_is_stable() -> None:
    content_hash = sha256_hex("hello")
    a = deterministic_chunk_id(doc_id="d", doc_version="1", content_hash=content_hash, chunk_index=0)
    b = deterministic_chunk_id(doc_id="d", doc_version="1", content_hash=content_hash, chunk_index=0)
    assert a == b

    # Qdrant point IDs must be UUID or int; we use UUID strings.
    uuid.UUID(a)
