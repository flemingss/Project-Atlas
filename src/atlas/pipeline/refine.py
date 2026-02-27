"""Refine node for Project Atlas pipeline (HLD section 2: Refine).

Uses vision model to improve document quality when judge score is low.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from atlas.diagnostics import ErrorCode, get_diagnostics
from atlas.llm.provider import ILlmProvider
from atlas.llm.provider import ChatMessage
from atlas.schemas import FidelityFlag, RefineResult


REFINE_SYSTEM_PROMPT = """You are a document refinement assistant.

Your ONLY permitted actions:
1. Fix OCR errors and typos
2. Repair broken markdown formatting (headings, lists, tables)
3. Preserve ALL original information — every section, heading, table,
   list item, and data point MUST appear in your output

You MUST NOT:
- Summarise, condense, or omit any content
- Add new information or commentary
- Restructure the document layout beyond fixing formatting errors
- Remove sections even if they seem redundant

Return ONLY the improved markdown. Do not add explanations."""

# Minimum ratio of refined output length to input length.  If the refined
# text is shorter than this fraction of the original the refinement is
# rejected and the original text is kept.  Configurable via
# ``thresholds.refine_min_preservation_ratio`` in pipeline.yaml.
_DEFAULT_MIN_PRESERVATION_RATIO = 0.6


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
    ):
        self.provider = provider
        self.model_name = model_name
        self.model_params = model_params
        self.max_retries = max_retries
        self.min_preservation_ratio = min_preservation_ratio
        self.diagnostics = get_diagnostics()

        # Create refine version identifier
        self.refine_version = f"{model_name}:v2"

    async def refine_document(
        self, *, markdown: str, judge_score: int, retry_count: int
    ) -> RefineResult:
        """Refine a markdown document using vision model.

        Args:
            markdown: The markdown content to refine
            judge_score: The score from the judge node
            retry_count: Current retry attempt (0-indexed)

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
                prompt = self._build_prompt(markdown, judge_score)

                # Call refinement model
                refined_markdown = await self._call_refine_model(prompt)

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

    def _build_prompt(self, markdown: str, judge_score: int) -> str:
        """Build refinement prompt.

        Note: the system prompt is sent separately via the ``system`` role
        message in :meth:`_call_refine_model`; we do NOT embed it in the
        user text to avoid sending the instructions twice.
        """
        return (
            f"Judge Score: {judge_score}/5 (needs improvement)\n\n"
            f"Original Document:\n{markdown}\n\n"
            f"Improved Document:"
        )

    async def _call_refine_model(self, prompt: str) -> str:
        """Call the refine model to improve the document."""
        messages = [
            ChatMessage(role="system", content=REFINE_SYSTEM_PROMPT),
            ChatMessage(role="user", content=prompt),
        ]
        return await self.provider.chat(model=self.model_name, messages=messages, params=self.model_params)

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
