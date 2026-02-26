"""Judge node for Project Atlas pipeline (HLD section 2: Judge).

Evaluates document quality using Llama 3.2 3B with explicit few-shot rubric.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from atlas.diagnostics import ErrorCode, get_diagnostics
from atlas.llm.provider import ILlmProvider
from atlas.llm.provider import ChatMessage
from atlas.schemas import JudgeResult


# Scoring dimensions evaluated by the judge rubric.
JUDGE_DIMENSIONS = ("faithfulness", "formatting", "cohesion", "hallucination_risk")

# Few-shot rubric for judge model (HLD section 2: Judge)
JUDGE_SYSTEM_PROMPT = """You are a document quality grader. Evaluate the given markdown document across four dimensions, each on a 1-5 scale.

Dimensions:
  FAITHFULNESS  – How accurately the markdown reproduces the source content (no missing/added info).
  FORMATTING    – Heading hierarchy, list structure, whitespace, and overall readability.
  COHESION      – Logical flow between sections; consistent terminology and tone.
  HALLUCINATION_RISK – Likelihood the text contains fabricated or hallucinated content (5 = no risk, 1 = high risk).

Per-dimension rubric:
  5 - Excellent  4 - Good  3 - Acceptable  2 - Poor  1 - Unusable

Provide your response in this exact format (one line per field):
FAITHFULNESS: [1-5]
FORMATTING: [1-5]
COHESION: [1-5]
HALLUCINATION_RISK: [1-5]
RATIONALE: [Your explanation in one sentence]
"""

JUDGE_FEW_SHOT_EXAMPLES = [
    {
        "input": "# Technical Manual\n\nThis document describes the system architecture...",
        "output": "FAITHFULNESS: 5\nFORMATTING: 5\nCOHESION: 5\nHALLUCINATION_RISK: 5\nRATIONALE: Clear structure with proper heading and complete readable content.",
    },
    {
        "input": "## Ov3rview\n\nThe syst3m c0nsists of...",
        "output": "FAITHFULNESS: 3\nFORMATTING: 4\nCOHESION: 3\nHALLUCINATION_RISK: 3\nRATIONALE: OCR errors present but content is still understandable.",
    },
    {
        "input": "�� �� sdfjk asdfj 123 �� ��",
        "output": "FAITHFULNESS: 1\nFORMATTING: 1\nCOHESION: 1\nHALLUCINATION_RISK: 1\nRATIONALE: Severe corruption with unreadable content.",
    },
]


def _prompt_hash() -> str:
    """Stable short hash of the judge prompt constants for versioning."""
    h = hashlib.sha256()
    h.update(JUDGE_SYSTEM_PROMPT.encode("utf-8"))
    for ex in JUDGE_FEW_SHOT_EXAMPLES:
        h.update(ex["input"].encode("utf-8"))
        h.update(ex["output"].encode("utf-8"))
    return h.hexdigest()[:12]


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

        # Create judge version identifier (model name + prompt hash for traceability)
        self.judge_version = f"{model_name}:ph-{_prompt_hash()}"

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
                sub_scores, rationale = self._parse_response(response)

                # Composite score is rounded mean of sub-dimensions
                score = round(sum(sub_scores.values()) / len(sub_scores)) if sub_scores else 3

                # Determine if refinement needed
                needs_refinement = score < judge_cutoff

                result = JudgeResult(
                    score=score,
                    confidence_rationale=rationale,
                    judge_version=self.judge_version,
                    needs_refinement=needs_refinement,
                    timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    sub_scores=sub_scores,
                )

                self.diagnostics.log_info(
                    component="judge",
                    message=f"Document graded: score={score}, sub_scores={sub_scores}, needs_refinement={needs_refinement}",
                    context={"score": score, "sub_scores": sub_scores, "rationale": rationale},
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
                    sub_scores={d: 1 for d in JUDGE_DIMENSIONS},
                )

    def _build_prompt(self, markdown: str) -> str:
        """Build user prompt with few-shot examples and document to grade."""
        examples_text = "\n\n".join(
            f"Example {i+1}:\nInput: {ex['input']}\nOutput: {ex['output']}"
            for i, ex in enumerate(JUDGE_FEW_SHOT_EXAMPLES)
        )

        prompt = f"""{examples_text}

Now grade this document:

{markdown}

Your response:"""
        return prompt

    async def _call_judge_model(self, prompt: str) -> str:
        """Call the judge LLM model with separated system and user messages."""
        messages = [
            ChatMessage(role="system", content=JUDGE_SYSTEM_PROMPT),
            ChatMessage(role="user", content=prompt),
        ]
        return await self.provider.chat(model=self.model_name, messages=messages, params=self.model_params)

    def _parse_response(self, response: str) -> tuple[dict[str, int], str]:
        """Parse judge model response to extract per-dimension scores and rationale.

        Returns (sub_scores dict, rationale string).  If none of the expected
        dimension lines can be parsed the method falls back to a legacy single-
        SCORE line for backwards compatibility.
        """
        sub_scores: dict[str, int] = {}
        rationale = "Unable to parse response"

        # Normalised dimension keys (upper-case) → canonical key
        dim_map = {d.upper(): d for d in JUDGE_DIMENSIONS}

        try:
            lines = response.strip().split("\n")
            for line in lines:
                if ":" not in line:
                    continue
                key, _, value = line.partition(":")
                key = key.strip().upper()
                value = value.strip()

                if key == "RATIONALE":
                    rationale = value
                elif key in dim_map:
                    try:
                        s = int(value)
                        sub_scores[dim_map[key]] = max(1, min(5, s))
                    except ValueError:
                        pass
                elif key == "SCORE" and not sub_scores:
                    # Legacy single-score fallback
                    try:
                        s = int(value)
                        # Spread the single score across all dimensions
                        for d in JUDGE_DIMENSIONS:
                            sub_scores[d] = max(1, min(5, s))
                    except ValueError:
                        pass
        except Exception as e:
            self.diagnostics.log_warning(
                component="judge",
                message=f"Failed to parse judge response: {e}",
                context={"response": response},
            )

        # If we still have no sub_scores, fall back to middle score
        if not sub_scores:
            for d in JUDGE_DIMENSIONS:
                sub_scores[d] = 3

        return sub_scores, rationale
