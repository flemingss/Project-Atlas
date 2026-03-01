"""Unified routing — decide the next pipeline step from rich context.

``decide_next_step`` is the single authoritative function for routing.
It consumes the full ``PipelineContext`` (judge sub_scores, cleanup
warnings, docling health, retry counts) plus pipeline config to produce
a :class:`RoutingDecision`.

Design principles:
- Pure function (no side-effects, no I/O).
- Deterministic given the same inputs.
- Easy to unit-test in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from atlas.pipeline.tokens import estimate_tokens, fits_in_context


# ---------------------------------------------------------------------------
# Routing decision dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RoutingDecision:
    """Outcome of routing logic.

    Attributes
    ----------
    target : str
        The target PipelineNode *value* (e.g. ``"judge"``, ``"failed"``).
    reason : str
        Short human-readable explanation of why this route was chosen.
    rollback : bool
        If ``True``, the caller should revert any state changes made by
        the most recent processing node (e.g. revert markdown after a
        score-regressing refine attempt).
    """

    target: str
    reason: str
    rollback: bool = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def decide_next_step(
    *,
    current_node: str,
    results: dict[str, Any],
    state_snapshot: dict[str, Any],
    config: dict[str, Any],
) -> RoutingDecision:
    """Determine the next pipeline node.

    Parameters
    ----------
    current_node:
        Value of ``PipelineNode`` (e.g. ``"ingest"``).
    results:
        ``PipelineContext.results`` — contains ``judge``, ``cleanup``,
        ``docling_health`` dicts when available.
    state_snapshot:
        Key scalars from ``DocumentIngestState``: ``refine_retries``,
        ``max_refine_retries``, ``needs_hitl``, ``mean_judge_score``.
    config:
        Pipeline config (``pipeline.yaml`` dict).
    """

    thresholds = config.get("thresholds", {})
    judge_cutoff = int(thresholds.get("judge_cutoff_refine", 4))
    fail_fast_score = int(thresholds.get("fail_fast_score", 0))  # 0 = disabled

    # ---- INGEST → CLEANUP ----
    if current_node == "ingest":
        # Check docling health for early fail-fast
        dh = results.get("docling_health", {})
        health_score = dh.get("health_score", 5)

        # Layout parser may supply extraction confidence directly
        extraction_meta = results.get("extraction_meta", {})
        ocr_conf = extraction_meta.get("mean_ocr_confidence")
        if ocr_conf is not None and ocr_conf < 0.3 and fail_fast_score:
            return RoutingDecision(
                target="failed",
                reason=f"Layout OCR confidence {ocr_conf:.2f} critically low",
            )

        if fail_fast_score and health_score <= fail_fast_score:
            return RoutingDecision(
                target="failed",
                reason=f"Docling health {health_score} ≤ fail_fast_score {fail_fast_score}",
            )
        return RoutingDecision(target="cleanup", reason="Standard ingest→cleanup")

    # ---- CLEANUP → JUDGE ----
    if current_node == "cleanup":
        # Check rule tags from config-driven cleanup rules (Phase 7A)
        cleanup = results.get("cleanup", {})
        rule_tags = cleanup.get("rule_tags", [])

        if "hard_failure" in rule_tags:
            return RoutingDecision(
                target="failed",
                reason="Cleanup rule tag 'hard_failure' — failing document",
            )
        if "suspicious_content" in rule_tags:
            return RoutingDecision(
                target="hitl",
                reason="Cleanup rule tag 'suspicious_content' — escalating to HITL",
            )
        return RoutingDecision(target="judge", reason="Standard cleanup→judge")

    # ---- JUDGE decision tree ----
    if current_node == "judge":
        judge = results.get("judge", {})
        composite = judge.get("score", 3)
        sub = judge.get("sub_scores", {})
        refine_retries = int(state_snapshot.get("refine_retries", 0))
        max_retries = int(state_snapshot.get("max_refine_retries", 2))
        needs_hitl = bool(state_snapshot.get("needs_hitl", False))
        score_history: list[int] = list(state_snapshot.get("judge_score_history", []))

        # ---- HITL-resume override ----
        # When a document was explicitly approved by a human reviewer and
        # the pipeline is re-running to commit chunks, skip all refine /
        # cleanup-rejudge / HITL-re-escalation logic.  The judge still
        # runs (its score feeds metadata tiering), but routing always
        # proceeds to metadata regardless of the score.
        if state_snapshot.get("is_hitl_resume"):
            return RoutingDecision(
                target="metadata",
                reason=f"HITL-approved document (score={composite}) — proceeding to metadata",
            )

        # Fail-fast: if composite is at or below hard floor, skip refine
        if fail_fast_score and composite <= fail_fast_score:
            return RoutingDecision(
                target="failed",
                reason=f"Composite judge score {composite} ≤ fail_fast threshold {fail_fast_score}",
            )

        # Per-dimension floor check: any dimension below its floor triggers refine.
        dim_floors: dict[str, int] = thresholds.get("judge_dim_floors", {})
        if dim_floors and sub:
            for dim, floor in dim_floors.items():
                if floor and sub.get(dim, 5) < floor and refine_retries < max_retries:
                    return RoutingDecision(
                        target="refine",
                        reason=f"Dimension '{dim}' score {sub.get(dim)} < floor {floor}",
                    )

        # Cleanup-and-rejudge: formatting bad but content OK → route back through cleanup
        cleanup_rejudge_enabled = bool(thresholds.get("cleanup_rejudge", False))
        cleanup_rejudge_done = int(state_snapshot.get("cleanup_rejudge_count", 0))
        if cleanup_rejudge_enabled and sub and cleanup_rejudge_done < 1:
            formatting_score = sub.get("formatting", composite)
            content_ok = all(
                sub.get(d, composite) >= judge_cutoff
                for d in ("faithfulness", "cohesion", "hallucination_risk")
            )
            if formatting_score < judge_cutoff and content_ok and refine_retries < max_retries:
                return RoutingDecision(
                    target="cleanup",
                    reason=f"Formatting sub-score {formatting_score} low but content OK — re-clean & re-judge",
                )

        # ----- M7: Score regression rollback -----
        # If a refine attempt made things WORSE (score dropped), stop
        # refining and proceed — the orchestrator will revert to the
        # pre-refine markdown.
        if len(score_history) >= 2 and refine_retries > 0:
            prev_score = score_history[-2]
            if composite < prev_score:
                # If the pre-refine score was itself below cutoff, escalate
                # to HITL rather than letting a bad document through.
                if prev_score < judge_cutoff:
                    return RoutingDecision(
                        target="hitl",
                        reason=(
                            f"Score regressed after refine ({prev_score}→{composite}) "
                            f"and pre-refine score still below cutoff; escalating"
                        ),
                        rollback=True,
                    )
                return RoutingDecision(
                    target="metadata",
                    reason=(
                        f"Score regressed after refine ({prev_score}→{composite}); "
                        f"reverting to pre-refine markdown and proceeding"
                    ),
                    rollback=True,
                )

        # ----- M6: Diminishing-returns detection -----
        # If the last refine attempt did NOT improve the score, stop
        # looping.  The LLM is unlikely to do better on a repeat.
        if len(score_history) >= 2 and refine_retries > 0:
            prev_score = score_history[-2]
            if composite <= prev_score and composite < judge_cutoff:
                return RoutingDecision(
                    target="hitl",
                    reason=(
                        f"Diminishing returns: score unchanged after refine "
                        f"({prev_score}→{composite}); escalating to HITL"
                    ),
                )

        # Standard refine path
        if composite < judge_cutoff and refine_retries < max_retries:
            # ----- Context-budget guard -----
            # If the document is too long for the refine model's context
            # window, skip full-document refinement.  Sectional refinement
            # (handled downstream in the orchestrator) may still proceed.
            markdown_len: int = int(state_snapshot.get("markdown_len", 0))
            max_ctx: int = int(config.get("limits", {}).get("max_context_tokens", 16384))
            if markdown_len and not fits_in_context(
                "x" * markdown_len, max_ctx
            ):
                return RoutingDecision(
                    target="refine",
                    reason=(
                        f"Composite {composite} < cutoff {judge_cutoff}, "
                        f"retries {refine_retries}/{max_retries} "
                        f"(document ~{estimate_tokens('x' * markdown_len)} tokens — "
                        f"sectional refinement required)"
                    ),
                )
            return RoutingDecision(
                target="refine",
                reason=f"Composite {composite} < cutoff {judge_cutoff}, retries {refine_retries}/{max_retries}",
            )

        # HITL review
        if needs_hitl or refine_retries >= max_retries:
            if composite < judge_cutoff:
                return RoutingDecision(
                    target="hitl",
                    reason=f"Retries exhausted ({refine_retries}/{max_retries}) and score still below cutoff",
                )

        return RoutingDecision(target="metadata", reason="Score acceptable — proceed to metadata")

    # ---- REFINE → JUDGE ----
    if current_node == "refine":
        return RoutingDecision(target="judge", reason="Post-refine re-evaluation")

    # ---- Linear tail: METADATA → EMBEDDINGS → CHUNKING → COMMIT → COMPLETED ----
    _linear = {
        "metadata": "embeddings",
        "embeddings": "chunking",
        "chunking": "commit",
        "commit": "completed",
    }
    if current_node in _linear:
        nxt = _linear[current_node]
        return RoutingDecision(target=nxt, reason=f"Linear {current_node}→{nxt}")

    # ---- HITL → COMPLETED ----
    if current_node == "hitl":
        return RoutingDecision(target="completed", reason="HITL resolved")

    return RoutingDecision(target="failed", reason=f"Unrecognised node '{current_node}'")
