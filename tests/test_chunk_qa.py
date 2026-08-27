"""Tests for atlas.rag.chunk_qa — post-chunking QA metrics and fallback."""

from __future__ import annotations

from atlas.rag.chunk_qa import chunk_with_fallback, validate_chunks
from atlas.rag.chunking import TextChunk

# ---------------------------------------------------------------------------
# validate_chunks
# ---------------------------------------------------------------------------

def _make_chunks(texts: list[str]) -> list[TextChunk]:
    return [TextChunk(index=i, text=t) for i, t in enumerate(texts)]


def test_validate_empty_chunks() -> None:
    qa = validate_chunks([], source_text="hello", configured_max_tokens=100)
    assert not qa.passed
    assert "no_chunks" in qa.violations


def test_validate_good_chunks() -> None:
    part_a = "Alpha bravo charlie. " * 10
    part_b = "Delta echo foxtrot. " * 10
    source = part_a + part_b
    chunks = _make_chunks([part_a, part_b])
    qa = validate_chunks(chunks, source_text=source, configured_max_tokens=200)
    assert qa.passed
    assert qa.chunk_count == 2
    assert qa.violations == []


def test_validate_oversize_chunk_fails() -> None:
    source = "word " * 100
    # One giant chunk that exceeds max_tokens by a lot.
    chunks = _make_chunks(["word " * 100])
    qa = validate_chunks(chunks, source_text=source, configured_max_tokens=10)
    assert not qa.passed
    assert any("max_token_ratio" in v for v in qa.violations)


def test_validate_duplicate_chunks_fail() -> None:
    source = "Hello world. " * 20
    # All chunks are identical → duplication_ratio = 0.8.
    chunks = _make_chunks(["Hello world."] * 5)
    qa = validate_chunks(
        chunks,
        source_text=source,
        configured_max_tokens=100,
        bounds={"max_duplication_ratio": 0.05},
    )
    assert not qa.passed
    assert any("duplication_ratio" in v for v in qa.violations)


def test_validate_low_coverage_fails() -> None:
    source = "word " * 200
    # Chunks cover very little of the source.
    chunks = _make_chunks(["word"])
    qa = validate_chunks(
        chunks,
        source_text=source,
        configured_max_tokens=100,
        bounds={"min_coverage_ratio": 0.80},
    )
    assert not qa.passed
    assert any("coverage_ratio" in v for v in qa.violations)


def test_qa_result_to_dict_keys() -> None:
    source = "Alpha " * 20
    chunks = _make_chunks(["Alpha " * 20])
    qa = validate_chunks(chunks, source_text=source, configured_max_tokens=100)
    d = qa.to_dict()
    assert "chunk_count" in d
    assert "avg_tokens" in d
    assert "passed" in d
    assert "violations" in d


# ---------------------------------------------------------------------------
# chunk_with_fallback
# ---------------------------------------------------------------------------

def test_chunk_with_fallback_semantic_passes() -> None:
    md = "# Heading\n\nSome body text here for testing.\n\n## Sub heading\n\nMore text here.\n"
    chunks, strategy, qa = chunk_with_fallback(
        text=md, strategy="semantic", target_tokens=320, max_tokens=400, max_chars=1000,
    )
    assert len(chunks) > 0
    assert strategy == "semantic"
    assert qa.passed


def test_chunk_with_fallback_paragraph_no_further_fallback() -> None:
    text = "Hello world. " * 50
    chunks, strategy, qa = chunk_with_fallback(
        text=text, strategy="paragraph", target_tokens=320, max_tokens=400, max_chars=500,
    )
    assert len(chunks) > 0
    # paragraph has no fallback.
    assert strategy == "paragraph"


def test_chunk_with_fallback_returns_strategy_used() -> None:
    text = "A paragraph.\n\nAnother paragraph.\n\nThird paragraph.\n"
    chunks, strategy, qa = chunk_with_fallback(
        text=text, strategy="semantic", target_tokens=320, max_tokens=400, max_chars=200,
    )
    assert strategy in ("semantic", "paragraph")
    assert len(chunks) > 0
