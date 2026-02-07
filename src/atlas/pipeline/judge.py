"""Judge node for Project Atlas pipeline (HLD section 2: Judge).

Evaluates document quality using Llama 3.2 3B with explicit few-shot rubric.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from atlas.diagnostics import ErrorCode, get_diagnostics
from atlas.llm.provider import ILlmProvider
from atlas.llm.provider import ChatMessage
from atlas.schemas import JudgeResult


# Few-shot rubric for judge model (HLD section 2: Judge)
JUDGE_SYSTEM_PROMPT = """You are a document quality grader. Grade the given markdown document on a scale of 1-5.

Grading Rubric:
5 - Excellent: Clean structure, clear headings, no OCR errors, complete information
4 - Good: Minor formatting issues, mostly readable, complete content
3 - Acceptable: Some OCR errors or formatting issues, content is understandable
2 - Poor: Significant OCR errors, unclear structure, missing information
1 - Unusable: Severe corruption, unreadable, or mostly gibberish

Provide your response in this exact format:
SCORE: [1-5]
RATIONALE: [Your explanation in one sentence]
"""

JUDGE_FEW_SHOT_EXAMPLES = [
    {
        "input": "# Technical Manual\n\nThis document describes the system architecture...",
        "output": "SCORE: 5\nRATIONALE: Clear structure with proper heading and complete readable content.",
    },
    {
        "input": "## Ov3rview\n\nThe syst3m c0nsists of...",
        "output": "SCORE: 3\nRATIONALE: OCR errors present but content is still understandable.",
    },
    {
        "input": "�� �� sdfjk asdfj 123 �� ��",
        "output": "SCORE: 1\nRATIONALE: Severe corruption with unreadable content.",
    },
]


class JudgeNode:
    """Judge node: Grade markdown quality using LLM (HLD section 2).

    Actions:
    - Grade Markdown projection (1-5 scale) using explicit few-shot rubric
    - Output confidence_rationale
    - Persist judge_version (prompt + model hash)
    - Determine if refinement is needed
    """

    def __init__(self, *, provider: ILlmProvider, model_name: str, model_params: dict[str, Any]):
        self.provider = provider
        self.model_name = model_name
        self.model_params = model_params
        self.diagnostics = get_diagnostics()

        # Create judge version identifier
        self.judge_version = f"{model_name}:v1"  # TODO: Add prompt hash

    async def grade_document(
        self, *, markdown: str, judge_cutoff: int = 4
    ) -> JudgeResult:
        """Grade a markdown document using the judge model.

        Returns JudgeResult with score, rationale, and refinement decision.
        """
        with self.diagnostics.trace_operation("judge_document", {"markdown_length": len(markdown)}):
            try:
                # Build prompt with few-shot examples
                prompt = self._build_prompt(markdown)

                # Call LLM (using chat completion)
                # NOTE: This is simplified - full implementation would use proper message format
                response = await self._call_judge_model(prompt)

                # Parse response
                score, rationale = self._parse_response(response)

                # Determine if refinement needed
                needs_refinement = score < judge_cutoff

                result = JudgeResult(
                    score=score,
                    confidence_rationale=rationale,
                    judge_version=self.judge_version,
                    needs_refinement=needs_refinement,
                    timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                )

                self.diagnostics.log_info(
                    component="judge",
                    message=f"Document graded: score={score}, needs_refinement={needs_refinement}",
                    context={"score": score, "rationale": rationale},
                )

                return result

            except Exception as e:
                self.diagnostics.log_error(
                    component="judge",
                    error_code=ErrorCode.JUDGE_MODEL_UNAVAILABLE,
                    message="Judge model failed",
                    exception=e,
                )
                # Return default low score on error
                return JudgeResult(
                    score=1,
                    confidence_rationale=f"Error during grading: {e}",
                    judge_version=self.judge_version,
                    needs_refinement=True,
                    timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                )

    def _build_prompt(self, markdown: str) -> str:
        """Build prompt with few-shot examples."""
        examples_text = "\n\n".join(
            f"Example {i+1}:\nInput: {ex['input']}\nOutput: {ex['output']}"
            for i, ex in enumerate(JUDGE_FEW_SHOT_EXAMPLES)
        )

        prompt = f"""{JUDGE_SYSTEM_PROMPT}

{examples_text}

Now grade this document:

{markdown}

Your response:"""
        return prompt

    async def _call_judge_model(self, prompt: str) -> str:
        """Call the judge LLM model.

        NOTE: Simplified implementation. Full version would use proper
        message format and handle streaming/errors better.
        """
        messages = [
            ChatMessage(role="system", content=JUDGE_SYSTEM_PROMPT),
            ChatMessage(role="user", content=prompt),
        ]
        return await self.provider.chat(model=self.model_name, messages=messages, params=self.model_params)

    def _parse_response(self, response: str) -> tuple[int, str]:
        """Parse judge model response to extract score and rationale."""
        score = 3  # Default
        rationale = "Unable to parse response"

        try:
            lines = response.strip().split("\n")
            for line in lines:
                if line.startswith("SCORE:"):
                    score_str = line.split(":", 1)[1].strip()
                    score = int(score_str)
                elif line.startswith("RATIONALE:"):
                    rationale = line.split(":", 1)[1].strip()
        except Exception as e:
            self.diagnostics.log_warning(
                component="judge",
                message=f"Failed to parse judge response: {e}",
                context={"response": response},
            )

        # Validate score range
        if score < 1 or score > 5:
            self.diagnostics.log_error(
                component="judge",
                error_code=ErrorCode.JUDGE_INVALID_SCORE,
                message=f"Invalid score: {score}",
            )
            score = 3  # Fallback to middle score

        return score, rationale
