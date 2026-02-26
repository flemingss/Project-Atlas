"""Docling health score — aggregate ingest quality into a single 1-5 score.

The health score distils PDF preflight metrics, extraction method quality,
and basic content statistics into a composite ``health_score`` plus per-
signal detail so downstream routing (judge, cleanup, fail-fast) can make
informed decisions without re-inspecting raw preflight data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class DoclingHealthResult:
    """Structured health assessment of a Docling ingest result."""

    health_score: int  # 1-5 composite
    signals: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_health(
    *,
    meta: dict[str, Any] | None,
    markdown_length: int,
    parse_profile: str | None = None,
) -> DoclingHealthResult:
    """Compute a 1-5 health score from Docling ingest metadata.

    Parameters
    ----------
    meta:
        The ``DoclingParseResult.meta`` dict (may be None for plain text).
    markdown_length:
        ``len(markdown_projection)`` – proxy for how much content was extracted.
    parse_profile:
        String representation of the ``ParseProfile`` enum, e.g. ``"pdf_text"``.

    Returns
    -------
    DoclingHealthResult with composite score, signals breakdown, and warnings.
    """
    warnings: list[str] = []
    signals: dict[str, Any] = {}

    meta = meta or {}
    preflight: dict[str, Any] = meta.get("pdf_preflight", {})
    extraction_method: str = meta.get("extraction_method", "unknown")

    # --- signal: extraction method quality ---
    method_scores = {
        "embedded_text": 5,
        "ocr": 3,
        "docling_default": 4,
        "unknown": 2,
    }
    method_score = method_scores.get(extraction_method, 2)
    signals["extraction_method"] = extraction_method
    signals["extraction_method_score"] = method_score

    if extraction_method == "ocr":
        warnings.append("OCR extraction may reduce fidelity")

    # --- signal: content volume ---
    if markdown_length == 0:
        content_score = 1
        warnings.append("Empty markdown — no text extracted")
    elif markdown_length < 50:
        content_score = 2
        warnings.append("Very short markdown (<50 chars)")
    elif markdown_length < 200:
        content_score = 3
    elif markdown_length < 2000:
        content_score = 4
    else:
        content_score = 5
    signals["markdown_length"] = markdown_length
    signals["content_score"] = content_score

    # --- signal: rotation ---
    has_rotation = bool(preflight.get("has_rotation"))
    mixed_rotation = bool(preflight.get("mixed_rotation"))
    rotation_score = 5
    if mixed_rotation:
        rotation_score = 2
        warnings.append("Mixed page rotations detected")
    elif has_rotation:
        rotation_score = 3
        warnings.append("Non-zero page rotation detected")
    signals["has_rotation"] = has_rotation
    signals["mixed_rotation"] = mixed_rotation
    signals["rotation_score"] = rotation_score

    # --- signal: text-as-shapes  ---
    text_as_shapes = bool(preflight.get("text_as_shapes_suspected"))
    shapes_score = 5
    if text_as_shapes:
        shapes_score = 1
        warnings.append("Text-as-shapes suspected — OCR likely needed")
    signals["text_as_shapes_suspected"] = text_as_shapes
    signals["shapes_score"] = shapes_score

    # --- signal: preflight text chars vs markdown length ---
    sample_text_chars = int(preflight.get("sample_text_chars", 0))
    signals["sample_text_chars"] = sample_text_chars

    # --- composite ---
    dimension_scores = [method_score, content_score, rotation_score, shapes_score]
    composite = round(sum(dimension_scores) / len(dimension_scores))
    composite = max(1, min(5, composite))

    return DoclingHealthResult(
        health_score=composite,
        signals=signals,
        warnings=warnings,
    )
