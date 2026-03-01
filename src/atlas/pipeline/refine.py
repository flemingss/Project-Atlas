"""Refine node for Project Atlas pipeline (HLD section 2: Refine).

Uses vision model to improve document quality when judge score is low.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from atlas.diagnostics import ErrorCode, get_diagnostics
from atlas.llm.openai_compat import strip_reasoning_tags
from atlas.llm.provider import ILlmProvider
from atlas.llm.provider import ChatMessage
from atlas.pipeline.tokens import count_headings, estimate_tokens, split_into_sections
from atlas.schemas import FidelityFlag, RefineResult

_log = logging.getLogger(__name__)


REFINE_SYSTEM_PROMPT = """You are a document refinement assistant.

Your ONLY permitted actions:
1. Fix OCR errors and typos
2. Repair broken markdown formatting (headings, lists, tables)
3. Preserve ALL original information — every section, heading, table,
   list item, and data point MUST appear in your output
4. Address specific weaknesses identified by the quality judge (see
   JUDGE FEEDBACK below)

You MUST NOT:
- Summarise, condense, or omit any content
- Add new information or commentary
- Restructure the document layout beyond fixing formatting errors
- Remove sections even if they seem redundant
- Add ANY conversational text before or after the document
- Wrap the output in markdown code fences (``` or ```markdown)
- Include preamble such as "Here is the improved document",
  "Sure, here's the refined version", "Below is the corrected text",
  or any similar introductory language
- Include postamble such as "Let me know if you need changes",
  "I hope this helps", "Feel free to ask", or any closing remarks
- Add meta-commentary sections like "Summary of Changes" or
  "Improvements Made" — these are NOT part of the document
- Use self-referential language ("As an AI", "I noticed", "I've cleaned")

Pay special attention to the per-dimension scores and rationale provided
in the JUDGE FEEDBACK section.  Focus your improvements on dimensions
scored below 4.

Your output must begin with the first line of the actual document content
(typically a heading or paragraph) and end with the last line of document
content. Nothing else.

Return ONLY the improved markdown. No explanations. No wrapper."""

# Minimum ratio of refined output length to input length.  If the refined
# text is shorter than this fraction of the original the refinement is
# rejected and the original text is kept.  Configurable via
# ``thresholds.refine_min_preservation_ratio`` in pipeline.yaml.
_DEFAULT_MIN_PRESERVATION_RATIO = 0.6


# ---------------------------------------------------------------------------
# Post-refine LLM artifact stripping (deterministic, Layer 1)
# ---------------------------------------------------------------------------
# Despite explicit prompt instructions, LLMs occasionally inject
# conversational preamble/postamble, code fences, or meta-commentary.
# These patterns are stripped deterministically from the raw model output
# before downstream guardrails run.

# Preamble lines: conversational openers at the start of output.
# Anchored to the very first non-blank line(s).
_PREAMBLE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"^(?:here\s+is|below\s+is|sure[,!]?\s|certainly[,!]?\s|of\s+course[,!]?\s"
        r"|i(?:'ve|'ve| have)\s+(?:made|improved|refined|cleaned|corrected|fixed|updated)"
        r"|the\s+(?:improved|refined|corrected|updated|cleaned)\s+(?:document|markdown|text|version)"
        r"|i\s+(?:noticed|found|see|observe|detected)"
        r"|as\s+(?:an?\s+ai|requested|instructed))",
        re.IGNORECASE,
    ),
]

# Postamble lines: conversational closers at the end of output.
_POSTAMBLE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"^(?:let\s+me\s+know|i\s+hope\s+this|feel\s+free|if\s+you\s+(?:need|want|have)"
        r"|please\s+(?:let|don't\s+hesitate)|don't\s+hesitate"
        r"|is\s+there\s+anything\s+else"
        r"|note:\s+i(?:'ve|'ve| have)\s+(?:made|kept|preserved)"
        r"|i\s+(?:made|kept|preserved)\s+(?:the\s+following|all|every)"
        r"|summary\s+of\s+(?:changes|improvements|modifications))",
        re.IGNORECASE,
    ),
]

# Meta-section headings that LLMs sometimes inject.
_META_HEADING_RE = re.compile(
    r"^#{1,3}\s+(?:summary\s+of\s+changes|improvements?\s+made"
    r"|changes?\s+(?:log|list|summary)|notes?\s+on\s+(?:changes|improvements)"
    r"|what\s+(?:was|i)\s+(?:changed|improved|fixed))\s*$",
    re.IGNORECASE,
)

# Whole-output markdown code fence wrapper.
_CODE_FENCE_WRAPPER_RE = re.compile(
    r"^```(?:markdown|md|text)?\s*\n(.*?)\n```\s*$",
    re.DOTALL,
)


def strip_llm_artifacts(text: str) -> str:
    """Strip common LLM conversational artifacts from refined output.

    This is a deterministic post-processing pass applied to raw LLM output
    before preservation guardrails.  It handles:

    1. Wrapping markdown code fences (```markdown … ```)
    2. Conversational preamble (first 1-3 lines)
    3. Conversational postamble (last 1-3 lines)
    4. Injected meta-commentary sections ("## Summary of Changes")

    Returns the cleaned text.  If stripping would reduce the text to <50%
    of the original, the original is returned unchanged (safety valve).
    """
    if not text or not text.strip():
        return text

    original_len = len(text.strip())
    cleaned = text

    # --- 1. Strip wrapping code fences ---
    m = _CODE_FENCE_WRAPPER_RE.match(cleaned.strip())
    if m:
        cleaned = m.group(1)
        _log.debug("strip_llm_artifacts: removed wrapping code fence")

    # --- 2. Strip preamble (up to 3 leading non-blank lines) ---
    lines = cleaned.split("\n")
    preamble_end = 0
    for i, line in enumerate(lines[:5]):  # scan first 5 lines max
        stripped = line.strip()
        if not stripped:
            continue  # skip blanks
        if any(p.search(stripped) for p in _PREAMBLE_PATTERNS):
            preamble_end = i + 1
        else:
            break  # stop at first non-matching non-blank line
    if preamble_end > 0:
        _log.debug("strip_llm_artifacts: removed %d preamble line(s)", preamble_end)
        lines = lines[preamble_end:]
        cleaned = "\n".join(lines)

    # --- 3. Strip postamble (up to 3 trailing non-blank lines) ---
    lines = cleaned.split("\n")
    postamble_start = len(lines)
    for i in range(len(lines) - 1, max(len(lines) - 6, -1), -1):
        stripped = lines[i].strip()
        if not stripped:
            continue
        if any(p.search(stripped) for p in _POSTAMBLE_PATTERNS):
            postamble_start = i
        else:
            break
    if postamble_start < len(lines):
        _log.debug(
            "strip_llm_artifacts: removed %d postamble line(s)",
            len(lines) - postamble_start,
        )
        lines = lines[:postamble_start]
        cleaned = "\n".join(lines)

    # --- 4. Strip injected meta-sections ---
    # Remove lines from a meta-heading through the next real heading or end
    out_lines: list[str] = []
    in_meta = False
    for line in cleaned.split("\n"):
        if _META_HEADING_RE.match(line.strip()):
            in_meta = True
            _log.debug("strip_llm_artifacts: removing meta-section '%s'", line.strip())
            continue
        if in_meta:
            # Exit meta-section when we hit a real heading
            if re.match(r"^#{1,6}\s+", line) and not _META_HEADING_RE.match(line.strip()):
                in_meta = False
                out_lines.append(line)
            # else: skip lines inside meta-section
            continue
        out_lines.append(line)
    cleaned = "\n".join(out_lines)

    # --- Safety valve ---
    # Protect against false-positive regex matches stripping real content.
    # Only trigger when: (a) the original is long enough for the ratio to
    # be meaningful (>200 chars), AND (b) stripping removed >50%.
    # For shorter texts (typical test/LLM outputs), artifact lines are
    # often a large fraction — stripping them is correct.
    cleaned = cleaned.strip()
    if (
        original_len > 200
        and len(cleaned) < original_len * 0.5
    ):
        _log.warning(
            "strip_llm_artifacts: stripping removed >50%% of content "
            "(%d→%d chars); keeping original",
            original_len,
            len(cleaned),
        )
        return text

    return cleaned


class RefineNode:
    """Refine node: Improve document quality using vision model (HLD section 2).

    Actions:
    - Triggered if Judge Score < 4
    - Constraint: Max 2 retries then move to HITL
    - Safety: Tag problematic chunks with fidelity_flag
    - Uses Llama 3.2 Vision for refinement
    """

    def __init__(
        self,
        *,
        provider: ILlmProvider,
        model_name: str,
        model_params: dict[str, Any],
        max_retries: int = 2,
        min_preservation_ratio: float = _DEFAULT_MIN_PRESERVATION_RATIO,
        min_section_ratio: float = 0.8,
        max_context_tokens: int = 16384,
        max_section_tokens: int = 6000,
    ):
        self.provider = provider
        self.model_name = model_name
        self.model_params = model_params
        self.max_retries = max_retries
        self.min_preservation_ratio = min_preservation_ratio
        self.min_section_ratio = min_section_ratio
        self.max_context_tokens = max_context_tokens
        self.max_section_tokens = max_section_tokens
        self.diagnostics = get_diagnostics()

        # Create refine version identifier
        self.refine_version = f"{model_name}:v2"

    async def refine_document(
        self,
        *,
        markdown: str,
        judge_score: int,
        retry_count: int,
        max_retries: int | None = None,
        judge_sub_scores: dict[str, int] | None = None,
        judge_rationale: str | None = None,
    ) -> RefineResult:
        """Refine a markdown document using vision model.

        Args:
            markdown: The markdown content to refine
            judge_score: The score from the judge node
            retry_count: Current retry attempt (0-indexed)
            max_retries: Maximum allowed retries (for iteration context)
            judge_sub_scores: Per-dimension scores from the judge node
            judge_rationale: Rationale text from the judge node

        Returns:
            RefineResult with refined markdown and metadata
        """
        with self.diagnostics.trace_operation(
            "refine_document",
            {"markdown_length": len(markdown), "judge_score": judge_score, "retry": retry_count},
        ):
            # Check if we've exceeded max retries
            if retry_count >= self.max_retries:
                self.diagnostics.log_error(
                    component="refine",
                    error_code=ErrorCode.REFINE_MAX_RETRIES,
                    message=f"Max refine retries ({self.max_retries}) exceeded",
                    context={"retry_count": retry_count},
                )
                return RefineResult(
                    refined_markdown=markdown,  # Return original
                    improvements_made=["Max retries exceeded"],
                    refine_version=self.refine_version,
                    success=False,
                    timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                )

            try:
                # Build refinement prompt
                prompt = self._build_prompt(
                    markdown,
                    judge_score,
                    retry_count=retry_count,
                    max_retries=max_retries or self.max_retries,
                    judge_sub_scores=judge_sub_scores,
                    judge_rationale=judge_rationale,
                )

                # Call refinement model with dynamic max_tokens based on
                # input length — allow up to 1.15× input tokens for the
                # output to avoid runaway generation while leaving room
                # for minor expansion from formatting fixes.
                input_est = estimate_tokens(markdown)
                dynamic_max_tokens = max(512, int(input_est * 1.15))
                refined_markdown = await self._call_refine_model(
                    prompt, max_tokens=dynamic_max_tokens
                )

                # ----------------------------------------------------------
                # Length-preservation guardrail: reject if the LLM
                # summarised or dropped significant content.
                # ----------------------------------------------------------
                input_len = len(markdown.strip())
                output_len = len(refined_markdown.strip())
                if input_len > 0 and output_len < input_len * self.min_preservation_ratio:
                    self.diagnostics.log_warning(
                        component="refine",
                        message=(
                            f"Refinement rejected — output too short "
                            f"({output_len}/{input_len} = "
                            f"{output_len / input_len:.0%}, "
                            f"threshold {self.min_preservation_ratio:.0%}). "
                            f"Keeping original text."
                        ),
                    )
                    return RefineResult(
                        refined_markdown=markdown,
                        improvements_made=["refinement_rejected:output_too_short"],
                        refine_version=self.refine_version,
                        success=False,
                        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    )

                # ----------------------------------------------------------
                # Section-count preservation guardrail: reject if the LLM
                # dropped entire sections (headings disappeared).
                # ----------------------------------------------------------
                input_headings = count_headings(markdown)
                output_headings = count_headings(refined_markdown)
                if (
                    input_headings >= 3  # Only check if there are enough headings to matter
                    and output_headings < input_headings * self.min_section_ratio
                ):
                    self.diagnostics.log_warning(
                        component="refine",
                        message=(
                            f"Refinement rejected — sections dropped "
                            f"({output_headings}/{input_headings} headings = "
                            f"{output_headings / input_headings:.0%}, "
                            f"threshold {self.min_section_ratio:.0%}). "
                            f"Keeping original text."
                        ),
                    )
                    return RefineResult(
                        refined_markdown=markdown,
                        improvements_made=["refinement_rejected:sections_dropped"],
                        refine_version=self.refine_version,
                        success=False,
                        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    )

                # Analyze improvements
                improvements = self._analyze_improvements(markdown, refined_markdown)

                result = RefineResult(
                    refined_markdown=refined_markdown,
                    improvements_made=improvements,
                    refine_version=self.refine_version,
                    success=True,
                    timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                )

                self.diagnostics.log_info(
                    component="refine",
                    message=f"Document refined successfully (attempt {retry_count + 1})",
                    context={"improvements": len(improvements)},
                )

                return result

            except Exception as e:
                self.diagnostics.log_error(
                    component="refine",
                    error_code=ErrorCode.REFINE_MODEL_ERROR,
                    message="Refine model failed",
                    exception=e,
                )
                return RefineResult(
                    refined_markdown=markdown,  # Return original
                    improvements_made=[],
                    refine_version=self.refine_version,
                    success=False,
                    timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                )

    async def refine_document_sectional(
        self,
        *,
        markdown: str,
        judge_score: int,
        retry_count: int,
        max_retries: int | None = None,
        judge_sub_scores: dict[str, int] | None = None,
        judge_rationale: str | None = None,
    ) -> RefineResult:
        """Refine a long document by splitting it into sections.

        Each section is refined independently with its own context
        budget, then the results are reassembled.  This avoids the
        context-window overflow that causes truncation/summarisation
        on documents longer than ~45% of the model's context limit.

        The method applies the same preservation guardrails as
        :meth:`refine_document` to the reassembled result.
        """
        self.diagnostics.log_info(
            component="refine",
            message=(
                f"Starting sectional refinement "
                f"(~{estimate_tokens(markdown)} tokens, "
                f"max_section={self.max_section_tokens})"
            ),
        )

        sections = split_into_sections(markdown, self.max_section_tokens)
        refined_sections: list[str] = []
        any_success = False

        for i, section in enumerate(sections):
            section_result = await self.refine_document(
                markdown=section,
                judge_score=judge_score,
                retry_count=retry_count,
                max_retries=max_retries,
                judge_sub_scores=judge_sub_scores,
                judge_rationale=judge_rationale,
            )
            if section_result.success:
                refined_sections.append(section_result.refined_markdown)
                any_success = True
            else:
                # Keep original section if refine failed for this section
                refined_sections.append(section)
                self.diagnostics.log_warning(
                    component="refine",
                    message=(
                        f"Sectional refine: section {i + 1}/{len(sections)} "
                        f"failed ({section_result.improvements_made}), "
                        f"keeping original"
                    ),
                )

        reassembled = "\n\n".join(refined_sections)

        # Apply whole-document guardrails to the reassembled result
        input_len = len(markdown.strip())
        output_len = len(reassembled.strip())
        if input_len > 0 and output_len < input_len * self.min_preservation_ratio:
            self.diagnostics.log_warning(
                component="refine",
                message=(
                    f"Sectional refinement rejected — reassembled output too short "
                    f"({output_len}/{input_len} = {output_len / input_len:.0%})"
                ),
            )
            return RefineResult(
                refined_markdown=markdown,
                improvements_made=["refinement_rejected:sectional_output_too_short"],
                refine_version=self.refine_version,
                success=False,
                timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            )

        input_headings = count_headings(markdown)
        output_headings = count_headings(reassembled)
        if (
            input_headings >= 3
            and output_headings < input_headings * self.min_section_ratio
        ):
            self.diagnostics.log_warning(
                component="refine",
                message=(
                    f"Sectional refinement rejected — sections dropped "
                    f"({output_headings}/{input_headings} headings)"
                ),
            )
            return RefineResult(
                refined_markdown=markdown,
                improvements_made=["refinement_rejected:sectional_sections_dropped"],
                refine_version=self.refine_version,
                success=False,
                timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            )

        improvements = self._analyze_improvements(markdown, reassembled)
        improvements.insert(0, f"sectional_refine:{len(sections)}_sections")

        self.diagnostics.log_info(
            component="refine",
            message=(
                f"Sectional refinement complete: "
                f"{len(sections)} sections, "
                f"{'some' if any_success else 'no'} improvements"
            ),
        )

        return RefineResult(
            refined_markdown=reassembled,
            improvements_made=improvements,
            refine_version=self.refine_version,
            success=any_success,
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )

    def _build_prompt(
        self,
        markdown: str,
        judge_score: int,
        *,
        retry_count: int = 0,
        max_retries: int = 2,
        judge_sub_scores: dict[str, int] | None = None,
        judge_rationale: str | None = None,
    ) -> str:
        """Build refinement prompt with rich judge feedback.

        Note: the system prompt is sent separately via the ``system`` role
        message in :meth:`_call_refine_model`; we do NOT embed it in the
        user text to avoid sending the instructions twice.
        """
        parts: list[str] = []

        # --- Iteration context ---
        parts.append(
            f"Attempt {retry_count + 1} of {max_retries} "
            f"({'final attempt — be thorough' if retry_count + 1 >= max_retries else 'earlier attempt — focus on biggest issues'})"
        )

        # --- Judge feedback ---
        parts.append(f"\nJUDGE FEEDBACK (composite {judge_score}/5):")
        if judge_sub_scores:
            weak_dims = []
            for dim, score in judge_sub_scores.items():
                label = dim.replace("_", " ").title()
                marker = " ← focus here" if score < 4 else ""
                parts.append(f"  {label}: {score}/5{marker}")
                if score < 4:
                    weak_dims.append(dim.replace("_", " ").title())
            if weak_dims:
                parts.append(f"Priority areas: {', '.join(weak_dims)}")
        else:
            parts.append(f"  Overall: {judge_score}/5")

        if judge_rationale:
            parts.append(f"Rationale: {judge_rationale}")

        # --- Document ---
        parts.append(f"\nDocument to improve:\n{markdown}")
        parts.append("\nImproved Document:")

        return "\n".join(parts)

    async def _call_refine_model(self, prompt: str, *, max_tokens: int | None = None) -> str:
        """Call the refine model to improve the document.

        Parameters
        ----------
        max_tokens:
            If provided, overrides any ``max_tokens`` already present in
            ``self.model_params`` for this single call.
        """
        params = dict(self.model_params)
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        messages = [
            ChatMessage(role="system", content=REFINE_SYSTEM_PROMPT),
            ChatMessage(role="user", content=prompt),
        ]
        result = await self.provider.chat(model=self.model_name, messages=messages, params=params)
        # Safety net: strip any <think> tags that survived the provider layer
        # (e.g. when using a non-OpenAICompatible provider in tests).
        result = strip_reasoning_tags(result)
        # Deterministic post-processing: strip conversational artifacts
        # (preamble, postamble, code fences, meta-sections) that the LLM
        # may inject despite explicit prompt instructions.
        return strip_llm_artifacts(result)

    def _analyze_improvements(self, original: str, refined: str) -> list[str]:
        """Analyze what improvements were made."""
        improvements = []

        if len(refined) != len(original):
            improvements.append("Length adjusted")

        if refined.count("#") != original.count("#"):
            improvements.append("Heading structure improved")

        if refined != original:
            improvements.append("Content refined")

        return improvements if improvements else ["No changes made"]

    def determine_fidelity_flag(
        self, *, judge_score: int, refine_success: bool, retry_count: int
    ) -> FidelityFlag:
        """Determine fidelity flag for a chunk based on processing results.

        HLD: Safety - Tag problematic chunks with fidelity_flag

        Priority order:
        1. NEEDS_REVIEW: max retries exceeded (must escalate to HITL)
        2. VERIFIED: judge score is high (>= 4), quality is good
        3. LOW_CONFIDENCE: judge score <= 2, quality is poor
        4. PARTIAL: score is borderline (3), neither clearly good nor clearly poor
        """
        if retry_count >= self.max_retries:
            return FidelityFlag.NEEDS_REVIEW

        if judge_score >= 4:
            return FidelityFlag.VERIFIED

        if judge_score <= 2:
            return FidelityFlag.LOW_CONFIDENCE

        return FidelityFlag.PARTIAL
