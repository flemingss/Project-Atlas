from __future__ import annotations

import uuid

from atlas.rag.chunking import chunk_text, chunk_text_hierarchical
from atlas.rag.deterministic import deterministic_chunk_id, sha256_hex


def test_chunk_text_respects_max_chars() -> None:
    text = "para1\n\n" + ("x" * 50) + "\n\npara3"
    chunks = chunk_text(text=text, max_chars=30)
    assert chunks
    assert all(len(c.text) <= 30 for c in chunks)


def test_deterministic_chunk_id_is_stable() -> None:
    content_hash = sha256_hex("hello")
    a = deterministic_chunk_id(
        tenant_id="t",
        project_id="p",
        corpus_id="c",
        doc_id="d",
        doc_version="1",
        content_hash=content_hash,
        chunk_index=0,
    )
    b = deterministic_chunk_id(
        tenant_id="t",
        project_id="p",
        corpus_id="c",
        doc_id="d",
        doc_version="1",
        content_hash=content_hash,
        chunk_index=0,
    )
    assert a == b

    # Qdrant point IDs must be UUID or int; we use UUID strings.
    uuid.UUID(a)


def test_chunk_text_hierarchical_basic():
    """Test hierarchical chunking with simple markdown."""
    text = """# Chapter 1

Introduction text here.

## Section 1.1

Some content in section 1.1.

## Section 1.2

Content in section 1.2.

# Chapter 2

Chapter 2 content."""

    chunks = chunk_text_hierarchical(text=text, max_chars=500)
    
    # Should have chunks with section paths
    assert len(chunks) > 0
    
    # Check that section paths are tracked
    has_section_path = any(len(c.section_path) > 0 for c in chunks)
    assert has_section_path


def test_chunk_text_hierarchical_section_paths():
    """Test that section paths are correctly tracked."""
    text = """# Chapter 1
## Section 1.1
### Subsection 1.1.1

Content here."""

    chunks = chunk_text_hierarchical(text=text, max_chars=500)
    
    # Find chunk with deepest nesting
    deepest = max(chunks, key=lambda c: len(c.section_path))
    
    # Should have nested path
    assert len(deepest.section_path) >= 2
    assert "Chapter 1" in deepest.section_path[0]


def test_chunk_text_hierarchical_parent_tracking():
    """Test that parent headers are tracked."""
    text = """# Main Heading

Content under main.

## Sub Heading

Content under sub."""

    chunks = chunk_text_hierarchical(text=text, max_chars=500)
    
    # Should have some chunks with parent_header_id
    has_parent = any(c.parent_header_id is not None for c in chunks)
    assert has_parent


def test_chunk_text_hierarchical_sibling_tracking():
    """Test that sibling relationships are tracked."""
    text = """# Chapter 1
## Section A

Content A.

## Section B

Content B.

## Section C

Content C."""

    chunks = chunk_text_hierarchical(text=text, max_chars=500)
    
    # Find chunks that should have siblings (same parent)
    chunks_with_siblings = [c for c in chunks if len(c.sibling_ids) > 0]
    
    # Should have at least some sibling relationships
    assert len(chunks_with_siblings) > 0


def test_chunk_text_hierarchical_empty_text():
    """Test hierarchical chunking with empty text."""
    chunks = chunk_text_hierarchical(text="", max_chars=100)
    assert len(chunks) == 0


def test_chunk_text_hierarchical_no_headings():
    """Test hierarchical chunking with text that has no headings."""
    text = "Just plain text without any headings.\n\nAnother paragraph."
    chunks = chunk_text_hierarchical(text=text, max_chars=100)
    
    # Should still create chunks
    assert len(chunks) > 0
    
    # But they should have empty section paths
    for c in chunks:
        assert len(c.section_path) == 0 or c.section_path == []


def test_chunk_text_hierarchical_deterministic_ids() -> None:
    """Hierarchical chunk indices must be deterministic for equal inputs."""
    text = "# Chapter\n\nSome content here.\n\n## Section\n\nMore content."
    chunks_a = chunk_text_hierarchical(text=text, max_chars=500)
    chunks_b = chunk_text_hierarchical(text=text, max_chars=500)
    assert len(chunks_a) == len(chunks_b)
    for a, b in zip(chunks_a, chunks_b):
        assert a.index == b.index
        assert a.text == b.text
        assert a.section_path == b.section_path


def test_chunk_text_hierarchical_preserves_section_hierarchy() -> None:
    """Section paths must reflect the heading nesting order."""
    text = "# Root\n\n## Child\n\n### Grandchild\n\nLeaf content."
    chunks = chunk_text_hierarchical(text=text, max_chars=500)
    # Find the chunk containing leaf content.
    leaf = next((c for c in chunks if "Leaf content" in c.text), None)
    assert leaf is not None
    # Its section_path should contain all ancestor headings.
    assert "Root" in leaf.section_path
    assert "Child" in leaf.section_path
