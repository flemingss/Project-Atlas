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


REFINE_SYSTEM_PROMPT = """You are a document refinement assistant. Improve the given markdown document by:
1. Fixing OCR errors
2. Improving structure and formatting
3. Clarifying unclear sections
4. Preserving all original information

Return only the improved markdown. Do not add explanations."""


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
    ):
        self.provider = provider
        self.model_name = model_name
        self.model_params = model_params
        self.max_retries = max_retries
        self.diagnostics = get_diagnostics()

        # Create refine version identifier
        self.refine_version = f"{model_name}:v1"

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
        """Build refinement prompt."""
        return f"""{REFINE_SYSTEM_PROMPT}

Judge Score: {judge_score}/5 (needs improvement)

Original Document:
{markdown}

Improved Document:"""

    async def _call_refine_model(self, prompt: str) -> str:
        """Call the refine model.

        NOTE: Simplified implementation. Full version would handle vision inputs.
        """
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
        """
        if judge_score >= 4 and refine_success:
            return FidelityFlag.VERIFIED

        if retry_count >= self.max_retries:
            return FidelityFlag.NEEDS_REVIEW

        if judge_score <= 2:
            return FidelityFlag.LOW_CONFIDENCE

        return FidelityFlag.PARTIAL
