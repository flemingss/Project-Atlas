from __future__ import annotations

import hashlib
import struct
from typing import Any

from atlas.llm.provider import ChatMessage, ILlmProvider


class DeterministicProvider(ILlmProvider):
    """Local, deterministic provider for dev/test.

    This provider is intended to allow exercising the pipeline (especially RAG)
    without requiring a running LLM server.

    - chat(): not implemented
    - embed(): produces stable vectors derived from SHA-256(text)

    Params:
      - dim: embedding dimension (default: 384)
    """

    async def chat(self, *, model: str, messages: list[ChatMessage], params: dict[str, Any]) -> str:
        # Deterministic, heuristic-based responses for pipeline/E2E.
        # The goal is to provide stable behavior for:
        # - Judge: return SCORE/RATIONALE
        # - Refine: return improved markdown
        # - Metadata: return JSON tags
        joined = "\n\n".join(f"{m.role.upper()}: {m.content}" for m in messages)
        text = (messages[-1].content if messages else "").strip()

        # Metadata detection
        if "Return JSON" in joined or "Return JSON format" in joined:
            return self._metadata_json(text)

        # Judge detection
        if "Grade the given markdown document" in joined or "Evaluate the given markdown document" in joined or "FAITHFULNESS:" in joined:
            scores = self._judge_sub_scores(text)
            rationale = self._judge_rationale(text, score=round(sum(scores.values()) / len(scores)))
            lines = [f"{k.upper()}: {v}" for k, v in scores.items()]
            lines.append(f"RATIONALE: {rationale}")
            return "\n".join(lines)

        # Refine detection
        if "document refinement" in joined.lower() or "Improved Document" in joined:
            return self._refine_markdown(text)

        # Rule-suggestion detection (Phase 7D)
        if "cleanup_rules" in joined and ("suggest" in joined.lower() or "cleanup rule" in joined.lower()):
            return self._suggest_rule_json()

        # Default: echo-ish deterministic response
        return f"OK ({model})"

    async def embed(self, *, model: str, texts: list[str], params: dict[str, Any]) -> list[list[float]]:
        dim = int((params or {}).get("dim", 384))
        if dim <= 0:
            raise ValueError(f"Invalid embedding dim: {dim}")
        return [self._embed_one(t, dim=dim) for t in texts]

    def _judge_sub_scores(self, markdown: str) -> dict[str, int]:
        """Return per-dimension judge scores for the given markdown."""
        base = self._judge_score(markdown)
        # For deterministic testing, derive slight per-dimension variation:
        # faithfulness = base, formatting = base, cohesion = base,
        # hallucination_risk mirrors base (high base → low risk → high score).
        return {
            "faithfulness": base,
            "formatting": base,
            "cohesion": base,
            "hallucination_risk": base,
        }

    def _judge_score(self, markdown: str) -> int:
        md = self._extract_judge_document(markdown)
        if not md:
            return 1

        # Explicit test hooks
        if "[UNFIXABLE]" in md:
            return 1
        if "[REFINED]" in md:
            return 5

        # Heuristics: headings + length tend higher
        non_ascii = sum(1 for ch in md if ord(ch) > 127)
        if non_ascii / max(len(md), 1) > 0.15:
            return 1

        # OCR-ish patterns (check before heading heuristics to catch corruption)
        if any(tok in md for tok in ("Ov3rview", "syst3m", "c0nsists")):
            return 3

        has_heading = "#" in md
        if has_heading and len(md) >= 80:
            return 5
        if has_heading:
            return 4

        return 4

    def _extract_judge_document(self, prompt: str) -> str:
        p = (prompt or "")
        # JudgeNode prompt uses: "Now grade this document:" then the markdown.
        if "Now grade this document:" in p:
            return p.split("Now grade this document:", 1)[1].strip()
        return p.strip()

    def _judge_rationale(self, markdown: str, *, score: int) -> str:
        if score >= 5:
            return "Clean structure and readable content."
        if score == 4:
            return "Minor formatting issues but readable."
        if score == 3:
            return "Some OCR artifacts present but understandable."
        if score == 2:
            return "Significant corruption reduces readability."
        return "Severe corruption with unreadable content."

    def _refine_markdown(self, prompt_or_md: str) -> str:
        # Extract original markdown if the prompt embeds it.
        src = prompt_or_md
        if "Original Document:" in src and "Improved Document:" in src:
            src = src.split("Original Document:", 1)[1].split("Improved Document:", 1)[0].strip()

        if "[UNFIXABLE]" in src:
            # Deliberately do not improve.
            return src

        cleaned = src.replace("Ov3rview", "Overview").replace("syst3m", "system").replace("c0nsists", "consists")
        if not cleaned.lstrip().startswith("#"):
            cleaned = "# Overview\n\n" + cleaned
        return "[REFINED]\n" + cleaned

    def _metadata_json(self, content: str) -> str:
        # Stable pseudo-tags derived from content hash.
        import json

        sample = (content or "")[:200]
        h = hashlib.sha256(sample.encode("utf-8")).hexdigest()[:8]
        payload = {
            "topic": f"deterministic-{h}",
            "keywords": ["deterministic", h],
            "density": "general",
        }
        return json.dumps(payload, sort_keys=True)

    def _suggest_rule_json(self) -> str:
        """Return a deterministic rule suggestion for CI/test usage."""
        import json

        payload = {
            "rule_yaml": (
                "- name: suggested_rule\n"
                "  match: {}\n"
                "  steps:\n"
                "    - kind: normalize_headings\n"
                "    - kind: merge_hardwrapped_paragraphs\n"
                "  tags:\n"
                "    - auto_fix_only\n"
            ),
            "rationale": "Deterministic suggestion: normalize headings and merge hard-wrapped paragraphs.",
        }
        return json.dumps(payload)

    def _embed_one(self, text: str, *, dim: int) -> list[float]:
        # Expand SHA-256 into a stream of bytes and interpret as uint32 words.
        # Map to [0, 1) floats for a stable, deterministic vector.
        out: list[float] = []
        counter = 0
        while len(out) < dim:
            h = hashlib.sha256()
            h.update(text.encode("utf-8"))
            h.update(struct.pack(">I", counter))
            digest = h.digest()
            for i in range(0, len(digest), 4):
                if len(out) >= dim:
                    break
                word = struct.unpack(">I", digest[i : i + 4])[0]
                out.append(word / 2**32)
            counter += 1
        return out
