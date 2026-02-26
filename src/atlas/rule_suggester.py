"""LLM-assisted cleanup rule suggestion — Phase 7D.

Provides an on-demand function that accepts sample markdown (or a
description of observed issues) and asks the configured LLM to propose
a ``cleanup_rules`` YAML snippet compatible with :mod:`atlas.pipeline.cleanup_rules`.

The suggestion is **advisory only** — operators review and manually add
the proposed rule to ``pipeline.yaml`` (or a DB config version).

Integration point:
    ``POST /admin/cleanup-rules/suggest``  (wired in ``api_admin.py``)

Design constraints:
    - Single synchronous LLM call (no background jobs).
    - Falls back to a heuristic suggestion when no LLM provider is available.
    - Output is a JSON object containing ``rule_yaml`` (string) and ``rationale``.
"""

from __future__ import annotations

import json
import logging
import textwrap
from typing import Any

from atlas.llm.provider import ChatMessage, ILlmProvider

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = textwrap.dedent("""\
    You are an expert document-cleanup engineer for "Project Atlas", a RAG
    pipeline appliance.  The operator will provide a sample of markdown
    text and a description of cleanup problems they observe.

    Your job is to suggest ONE cleanup rule in YAML that fixes the
    described problems.  The rule must conform to the Atlas cleanup_rules
    schema:

    ```yaml
    - name: <short_slug>
      match:                           # all fields optional
        tenant_id: <str>
        project_id: <str>
        corpus_id: <str>
        mime_type: <str>
        filename_pattern: <glob>
      steps:                           # at least one step
        - kind: strip_lines_matching
          pattern: <regex>
        - kind: rewrite_pattern
          pattern: <regex>
          replacement: <str>
        - kind: strip_headers_footers
          first_n: <int>
          last_n: <int>
          patterns:
            - <regex>
        - kind: normalize_headings
        - kind: merge_hardwrapped_paragraphs
        - kind: fix_bullets
          marker: "-"
      tags:                            # optional
        - auto_fix_only
        - suspicious_content
        - hard_failure
    ```

    Reply with **only** a JSON object (no markdown fences) with two keys:
    - "rule_yaml": the suggested rule as a YAML string (ready to paste into
      pipeline.yaml under cleanup_rules).
    - "rationale": a brief explanation (1-3 sentences) of why you chose
      these steps.

    If you cannot determine a useful rule, return:
    {"rule_yaml": "", "rationale": "Not enough information to suggest a rule."}
""")


# ---------------------------------------------------------------------------
# Heuristic fallback (no LLM needed)
# ---------------------------------------------------------------------------

def _heuristic_suggestion(
    markdown_sample: str,
    issues: str,
    context: dict[str, str],
) -> dict[str, Any]:
    """Best-effort deterministic suggestion when no LLM is available."""
    steps: list[dict[str, Any]] = []
    reasons: list[str] = []

    combined = (markdown_sample + " " + issues).lower()

    # Detect hard-wrapped paragraphs (many short lines without blank separators)
    lines = markdown_sample.split("\n")
    non_blank = [ln for ln in lines if ln.strip()]
    if non_blank:
        avg_len = sum(len(ln) for ln in non_blank) / len(non_blank)
        if avg_len < 80 and len(non_blank) > 5:
            steps.append({"kind": "merge_hardwrapped_paragraphs"})
            reasons.append("Short average line length suggests hard-wrapped paragraphs.")

    # Detect mixed bullets
    bullet_markers = set()
    for ln in lines:
        stripped = ln.lstrip()
        if stripped[:2] in ("* ", "- ", "+ "):
            bullet_markers.add(stripped[0])
    if len(bullet_markers) > 1:
        steps.append({"kind": "fix_bullets", "marker": "-"})
        reasons.append("Multiple bullet markers detected.")

    # Detect setext headings
    for i, ln in enumerate(lines):
        if i > 0 and lines[i - 1].strip() and (ln.strip().startswith("===") or ln.strip().startswith("---")):
            steps.append({"kind": "normalize_headings"})
            reasons.append("Setext-style headings found; converting to ATX.")
            break

    # Keyword-based hints from the issues description
    if any(kw in combined for kw in ("header", "footer", "page number", "watermark")):
        steps.append({"kind": "strip_headers_footers", "patterns": [r"^Page \d+$"]})
        reasons.append("Issue mentions headers/footers/page numbers.")

    if any(kw in combined for kw in ("ocr", "garbled", "noise", "artifact")):
        steps.append({"kind": "strip_lines_matching", "pattern": r"^[^a-zA-Z]*$"})
        reasons.append("Issue mentions OCR artifacts or garbled text.")

    if not steps:
        return {
            "rule_yaml": "",
            "rationale": "Could not determine a useful rule from the provided sample and issues.",
        }

    # Build the YAML string
    import yaml  # local import to keep module lightweight

    match_block: dict[str, str] = {}
    if context.get("corpus_id"):
        match_block["corpus_id"] = context["corpus_id"]
    if context.get("mime_type"):
        match_block["mime_type"] = context["mime_type"]

    rule = {
        "name": "suggested_rule",
        "match": match_block or {},
        "steps": steps,
        "tags": ["auto_fix_only"],
    }
    rule_yaml = yaml.dump([rule], default_flow_style=False, sort_keys=False).strip()

    return {
        "rule_yaml": rule_yaml,
        "rationale": " ".join(reasons),
    }


# ---------------------------------------------------------------------------
# LLM-assisted suggestion
# ---------------------------------------------------------------------------

async def suggest_cleanup_rule(
    *,
    provider: ILlmProvider,
    model: str,
    markdown_sample: str,
    issues: str = "",
    context: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ask the LLM to suggest a cleanup rule.

    Parameters
    ----------
    provider:
        An ``ILlmProvider`` instance (could be deterministic or real).
    model:
        The model name to call via ``provider.chat()``.
    markdown_sample:
        A representative markdown snippet exhibiting the problem.
    issues:
        Free-text description of observed cleanup problems.
    context:
        Optional scope hints (``tenant_id``, ``project_id``, ``corpus_id``,
        ``mime_type``) to inform the match block.
    params:
        Extra provider params forwarded to ``chat()``.

    Returns
    -------
    dict with keys ``rule_yaml`` (str) and ``rationale`` (str).
    """
    context = context or {}
    params = params or {}

    user_parts: list[str] = []
    if markdown_sample.strip():
        user_parts.append(f"### Sample markdown\n\n```\n{markdown_sample[:4000]}\n```")
    if issues.strip():
        user_parts.append(f"### Observed issues\n\n{issues.strip()}")
    if context:
        ctx_lines = [f"- {k}: {v}" for k, v in context.items() if v]
        if ctx_lines:
            user_parts.append("### Document context\n\n" + "\n".join(ctx_lines))

    if not user_parts:
        return {
            "rule_yaml": "",
            "rationale": "No sample or issues provided.",
        }

    user_content = "\n\n".join(user_parts)

    messages = [
        ChatMessage(role="system", content=_SYSTEM_PROMPT),
        ChatMessage(role="user", content=user_content),
    ]

    try:
        raw = await provider.chat(model=model, messages=messages, params=params)
    except Exception:
        log.warning("LLM call failed for rule suggestion — falling back to heuristic", exc_info=True)
        return _heuristic_suggestion(markdown_sample, issues, context)

    # Parse the LLM response
    return _parse_llm_response(raw, markdown_sample, issues, context)


def _sanitize_suggestion(result: dict[str, Any]) -> dict[str, Any]:
    """Validate the suggested rule YAML, appending warnings if invalid."""
    rule_yaml = result.get("rule_yaml", "")
    if not rule_yaml:
        return result

    import yaml as _yaml
    from atlas.startup_validation import validate_cleanup_rules

    try:
        parsed = _yaml.safe_load(rule_yaml)
    except Exception as exc:
        result["validation_errors"] = [f"YAML parse error: {exc}"]
        result["rationale"] += " ⚠️ The suggested YAML has syntax errors — review before using."
        return result

    # Normalize: if the LLM returned a single dict instead of a list, wrap it.
    if isinstance(parsed, dict):
        parsed = [parsed]

    if not isinstance(parsed, list):
        result["validation_errors"] = ["Expected a YAML list of rule entries"]
        result["rationale"] += " ⚠️ The suggested YAML has structural errors — review before using."
        return result

    errors = validate_cleanup_rules(parsed)
    if errors:
        result["validation_errors"] = errors
        result["rationale"] += (
            f" ⚠️ {len(errors)} validation issue(s) detected — review before pasting into pipeline.yaml."
        )
    else:
        result["validation_errors"] = []

    return result


def _parse_llm_response(
    raw: str,
    markdown_sample: str,
    issues: str,
    context: dict[str, str],
) -> dict[str, Any]:
    """Extract JSON from the LLM response, with fallback."""
    text = raw.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last fence lines
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        result = json.loads(text)
        if isinstance(result, dict) and "rule_yaml" in result:
            return _sanitize_suggestion({
                "rule_yaml": str(result.get("rule_yaml", "")),
                "rationale": str(result.get("rationale", "")),
            })
    except (json.JSONDecodeError, TypeError):
        pass

    # If the LLM didn't return valid JSON, fall back to heuristic
    log.warning("LLM response was not valid JSON — falling back to heuristic")
    return _sanitize_suggestion(_heuristic_suggestion(markdown_sample, issues, context))
