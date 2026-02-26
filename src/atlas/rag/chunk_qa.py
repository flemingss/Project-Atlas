"""Post-chunking quality-assurance metrics and automatic fallback.

After the primary chunking pass, ``validate_chunks`` computes QA metrics and
compares them against configurable bounds.  If QA fails and the current
strategy allows fallback, :func:`chunk_with_fallback` transparently retries
with a simpler chunker.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from atlas.rag.chunking import (
    TextChunk,
    _approx_tokens,
    chunk_markdown_semantic,
    chunk_text,
    chunk_text_hierarchical,
)

log = logging.getLogger(__name__)

# Strategy fallback chain: semantic → paragraph.  hierarchical → paragraph.
_FALLBACK_CHAIN: dict[str, str | None] = {
    "semantic": "paragraph",
    "hierarchical": "paragraph",
    "paragraph": None,
}


# ---------------------------------------------------------------------------
# QA metrics
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChunkQAResult:
    """Quality metrics for a set of chunks produced from a single document."""

    chunk_count: int
    avg_tokens: float
    max_tokens: int
    min_tokens: int
    coverage_ratio: float  # sum(chunk_chars) / source_chars
    max_token_ratio: float  # max_tokens / configured_max_tokens (>1 = oversize)
    duplication_ratio: float  # 1 − unique_chunk_texts / total
    passed: bool
    violations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_count": self.chunk_count,
            "avg_tokens": round(self.avg_tokens, 1),
            "max_tokens": self.max_tokens,
            "min_tokens": self.min_tokens,
            "coverage_ratio": round(self.coverage_ratio, 3),
            "max_token_ratio": round(self.max_token_ratio, 3),
            "duplication_ratio": round(self.duplication_ratio, 3),
            "passed": self.passed,
            "violations": self.violations,
        }


# Configurable bounds (defaults; overridable from pipeline.yaml chunking.qa section).
_DEFAULT_BOUNDS: dict[str, float] = {
    "min_chunk_count": 1,
    "max_token_ratio_limit": 1.25,  # chunk may exceed configured max by up to 25 %
    "max_duplication_ratio": 0.10,
    "min_coverage_ratio": 0.80,
}


def validate_chunks(
    chunks: list[TextChunk],
    *,
    source_text: str,
    configured_max_tokens: int,
    bounds: dict[str, float] | None = None,
) -> ChunkQAResult:
    """Compute QA metrics for *chunks* and check them against *bounds*."""
    bnds = {**_DEFAULT_BOUNDS, **(bounds or {})}
    violations: list[str] = []

    if not chunks:
        return ChunkQAResult(
            chunk_count=0,
            avg_tokens=0.0,
            max_tokens=0,
            min_tokens=0,
            coverage_ratio=0.0,
            max_token_ratio=0.0,
            duplication_ratio=0.0,
            passed=False,
            violations=["no_chunks"],
        )

    token_counts = [_approx_tokens(c.text) for c in chunks]
    total_chunk_chars = sum(len(c.text) for c in chunks)
    source_chars = max(len(source_text.strip()), 1)

    avg_tok = sum(token_counts) / len(token_counts)
    max_tok = max(token_counts)
    min_tok = min(token_counts)
    coverage = total_chunk_chars / source_chars
    max_tok_ratio = max_tok / max(configured_max_tokens, 1)

    unique_texts = len({c.text for c in chunks})
    dup_ratio = 1.0 - (unique_texts / len(chunks)) if len(chunks) > 0 else 0.0

    # Check bounds.
    if len(chunks) < int(bnds.get("min_chunk_count", 1)):
        violations.append(f"chunk_count={len(chunks)} < min={int(bnds['min_chunk_count'])}")

    if max_tok_ratio > float(bnds.get("max_token_ratio_limit", 1.25)):
        violations.append(
            f"max_token_ratio={max_tok_ratio:.2f} > limit={float(bnds['max_token_ratio_limit']):.2f}"
        )

    if dup_ratio > float(bnds.get("max_duplication_ratio", 0.10)):
        violations.append(
            f"duplication_ratio={dup_ratio:.3f} > limit={float(bnds['max_duplication_ratio']):.3f}"
        )

    if coverage < float(bnds.get("min_coverage_ratio", 0.80)):
        violations.append(
            f"coverage_ratio={coverage:.3f} < min={float(bnds['min_coverage_ratio']):.3f}"
        )

    return ChunkQAResult(
        chunk_count=len(chunks),
        avg_tokens=avg_tok,
        max_tokens=max_tok,
        min_tokens=min_tok,
        coverage_ratio=coverage,
        max_token_ratio=max_tok_ratio,
        duplication_ratio=dup_ratio,
        passed=len(violations) == 0,
        violations=violations,
    )


# ---------------------------------------------------------------------------
# Chunk-with-fallback
# ---------------------------------------------------------------------------

def _run_strategy(
    strategy: str,
    text: str,
    target_tokens: int,
    max_tokens: int,
    max_chars: int,
) -> list[TextChunk]:
    """Dispatch to the correct chunker for *strategy*."""
    if strategy == "paragraph":
        return chunk_text(text=text, max_chars=max_chars)
    if strategy == "hierarchical":
        return chunk_text_hierarchical(text=text, max_chars=max_chars)
    return chunk_markdown_semantic(text=text, target_tokens=target_tokens, max_tokens=max_tokens)


def chunk_with_fallback(
    *,
    text: str,
    strategy: str,
    target_tokens: int = 320,
    max_tokens: int = 400,
    max_chars: int = 1000,
    qa_bounds: dict[str, float] | None = None,
) -> tuple[list[TextChunk], str, ChunkQAResult]:
    """Chunk *text* using *strategy*, falling back if QA fails.

    Returns ``(chunks, strategy_used, qa_result)`` — *strategy_used* may differ
    from the requested *strategy* if a fallback was triggered.
    """
    current = strategy
    while current is not None:
        chunks = _run_strategy(current, text, target_tokens, max_tokens, max_chars)
        qa = validate_chunks(
            chunks,
            source_text=text,
            configured_max_tokens=max_tokens if current != "paragraph" else (max_chars // 4),
            bounds=qa_bounds,
        )
        if qa.passed:
            return chunks, current, qa

        fallback = _FALLBACK_CHAIN.get(current)
        if fallback is None:
            log.warning(
                "Chunk QA failed for strategy=%s with no fallback available: %s",
                current,
                qa.violations,
            )
            return chunks, current, qa

        log.info(
            "Chunk QA failed for strategy=%s (%s); falling back to %s",
            current,
            qa.violations,
            fallback,
        )
        current = fallback

    # Should never reach here, but satisfy type checker.
    return chunks, strategy, qa  # type: ignore[possibly-undefined]
