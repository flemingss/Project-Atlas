"""Refine node for Project Atlas pipeline (HLD section 2: Refine).

Uses vision model to improve document quality when judge score is low.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from atlas.diagnostics import ErrorCode, get_diagnostics
from atlas.llm.openai_compat import strip_reasoning_tags
from atlas.llm.provider import ChatMessage, ILlmProvider
from atlas.pipeline.guardrails import dropped_facts, strip_llm_artifacts
from atlas.pipeline.tokens import count_headings, estimate_tokens, split_into_sections
from atlas.schemas import RefineResult

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
# 0.85 matches thresholds.refine_min_preservation_ratio in pipeline.yaml and
# the runner's fallback. Kept in sync deliberately: a class default that
# disagrees with the shipped config means a directly-constructed RefineNode
# (tests, ad-hoc use) silently enforces a weaker guard than production.
_DEFAULT_MIN_PRESERVATION_RATIO = 0.85


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
        fact_preservation: bool = True,
        max_context_tokens: int = 16384,
        max_section_tokens: int = 6000,
        max_output_tokens: int | None = None,
    ):
        self.provider = provider
        self.model_name = model_name
        self.model_params = model_params
        self.max_retries = max_retries
        self.min_preservation_ratio = min_preservation_ratio
        self.min_section_ratio = min_section_ratio
        # Reject a refinement that loses any digit-bearing token (phone-number
        # groups, part numbers, model names, quantities). Length and heading
        # counts cannot see a single dropped digit; this can, and a wrong
        # number is the worst thing a refine pass can put into a RAG corpus.
        self.fact_preservation = fact_preservation
        self.max_context_tokens = max_context_tokens
        self.max_section_tokens = max_section_tokens
        # Declared response ceiling for this model. Refine emits a full rewrite,
        # so the response is about as long as the input — this, not the context
        # window, is what limits how large a document can be refined in one pass.
        self.max_output_tokens = max_output_tokens
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
                    timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
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
                        timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
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
                        timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    )

                # ----------------------------------------------------------
                # Fact-preservation guardrail: reject if any digit-bearing
                # token disappeared (a dropped digit, a lost part number).
                # ----------------------------------------------------------
                rejected = self._reject_if_facts_dropped(markdown, refined_markdown)
                if rejected is not None:
                    return rejected

                # Analyze improvements
                improvements = self._analyze_improvements(markdown, refined_markdown)

                result = RefineResult(
                    refined_markdown=refined_markdown,
                    improvements_made=improvements,
                    refine_version=self.refine_version,
                    success=True,
                    timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
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
                    timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
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
                timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
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
                timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            )

        rejected = self._reject_if_facts_dropped(markdown, reassembled, sectional=True)
        if rejected is not None:
            return rejected

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
            timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
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

    def _reject_if_facts_dropped(
        self, markdown: str, refined: str, *, sectional: bool = False
    ) -> RefineResult | None:
        """Return a rejection result if *refined* lost any fact token, else None.

        Shared by the holistic and sectional paths so both enforce the same
        rule on the whole document. See :func:`atlas.pipeline.guardrails.dropped_facts`.
        """
        if not self.fact_preservation:
            return None
        missing = dropped_facts(markdown, refined)
        if not missing:
            return None
        label = "sectional_facts_dropped" if sectional else "facts_dropped"
        self.diagnostics.log_warning(
            component="refine",
            message=(
                f"{'Sectional refinement' if sectional else 'Refinement'} rejected — "
                f"{len(missing)}+ digit-bearing tokens missing from output "
                f"(e.g. {', '.join(missing[:5])}). Keeping original text."
            ),
        )
        return RefineResult(
            refined_markdown=markdown,
            improvements_made=[f"refinement_rejected:{label}"],
            refine_version=self.refine_version,
            success=False,
            timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )

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
