"""Tests for Phase 7D — LLM-assisted cleanup rule suggestion.

Covers:
- ``suggest_cleanup_rule()`` with the deterministic provider.
- The heuristic fallback (``_heuristic_suggestion``).
- ``POST /admin/cleanup-rules/suggest`` endpoint.
- The deterministic provider's ``_suggest_rule_json`` branch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from atlas.llm.deterministic import DeterministicProvider
from atlas.llm.provider import ChatMessage
from atlas.rule_suggester import _heuristic_suggestion, suggest_cleanup_rule
from tests.helpers import make_test_app

# ---------------------------------------------------------------------------
# Unit tests — suggest_cleanup_rule with DeterministicProvider
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_suggest_cleanup_rule_deterministic() -> None:
    """The deterministic provider returns a valid JSON-structured suggestion."""
    provider = DeterministicProvider()
    result = await suggest_cleanup_rule(
        provider=provider,
        model="deterministic-refine",
        markdown_sample="Some sample\nmarkdown text",
        issues="headings are inconsistent",
    )
    assert "rule_yaml" in result
    assert "rationale" in result
    assert isinstance(result["rule_yaml"], str)
    assert len(result["rule_yaml"]) > 0
    assert "suggested_rule" in result["rule_yaml"]


@pytest.mark.asyncio
async def test_suggest_cleanup_rule_empty_input() -> None:
    """With empty sample AND issues, returns early with empty rule_yaml."""
    provider = DeterministicProvider()
    result = await suggest_cleanup_rule(
        provider=provider,
        model="deterministic-refine",
        markdown_sample="",
        issues="",
    )
    assert result["rule_yaml"] == ""
    assert "No sample" in result["rationale"] or "no" in result["rationale"].lower()


@pytest.mark.asyncio
async def test_suggest_cleanup_rule_with_context() -> None:
    """Context dict is forwarded and doesn't break the call."""
    provider = DeterministicProvider()
    result = await suggest_cleanup_rule(
        provider=provider,
        model="deterministic-refine",
        markdown_sample="# Title\n\nSome content",
        issues="page numbers in footers",
        context={"corpus_id": "corp1", "mime_type": "application/pdf"},
    )
    assert "rule_yaml" in result
    assert len(result["rule_yaml"]) > 0


# ---------------------------------------------------------------------------
# Unit tests — heuristic fallback
# ---------------------------------------------------------------------------

def test_heuristic_hardwrap_detection() -> None:
    """Short average line length triggers merge_hardwrapped_paragraphs."""
    sample = "\n".join(["This is a short line"] * 10)
    result = _heuristic_suggestion(sample, "", {})
    assert "merge_hardwrapped_paragraphs" in result["rule_yaml"]


def test_heuristic_mixed_bullets() -> None:
    """Multiple bullet markers trigger fix_bullets."""
    sample = "- item one\n* item two\n+ item three\n"
    result = _heuristic_suggestion(sample, "", {})
    assert "fix_bullets" in result["rule_yaml"]


def test_heuristic_setext_headings() -> None:
    """Setext-style headings trigger normalize_headings."""
    sample = "Title\n=====\n\nSome body text"
    result = _heuristic_suggestion(sample, "", {})
    assert "normalize_headings" in result["rule_yaml"]


def test_heuristic_header_footer_keywords() -> None:
    """Issues mentioning 'header'/'footer' trigger strip_headers_footers."""
    result = _heuristic_suggestion("Some text\nPage 1\nMore text", "page number footer", {})
    assert "strip_headers_footers" in result["rule_yaml"]


def test_heuristic_ocr_keywords() -> None:
    """Issues mentioning 'OCR artifacts' trigger strip_lines_matching."""
    result = _heuristic_suggestion("Some text", "ocr artifacts and noise", {})
    assert "strip_lines_matching" in result["rule_yaml"]


def test_heuristic_no_signal() -> None:
    """When nothing is detectable, returns empty rule_yaml."""
    result = _heuristic_suggestion("Normal paragraph that is long enough to avoid short-line detection.", "", {})
    assert result["rule_yaml"] == ""


def test_heuristic_context_in_match() -> None:
    """Context dict populates the match block in the YAML."""
    sample = "\n".join(["Short line"] * 10)
    result = _heuristic_suggestion(sample, "", {"corpus_id": "corp1", "mime_type": "text/plain"})
    assert "corpus_id" in result["rule_yaml"]
    assert "corp1" in result["rule_yaml"]


# ---------------------------------------------------------------------------
# Deterministic provider branch test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deterministic_provider_suggest_branch() -> None:
    """The deterministic provider detects rule-suggestion prompts."""
    provider = DeterministicProvider()
    # The detection key is "cleanup_rules" in the joined messages plus
    # "suggest" or "cleanup rule" in lower-case.
    messages = [
        ChatMessage(role="system", content="cleanup_rules schema"),
        ChatMessage(role="user", content="Please suggest a cleanup rule"),
    ]
    result = await provider.chat(model="test", messages=messages, params={})
    parsed = json.loads(result)
    assert "rule_yaml" in parsed
    assert "rationale" in parsed
    assert "suggested_rule" in parsed["rule_yaml"]


# ---------------------------------------------------------------------------
# Integration test — API endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cleanup_rules_suggest_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /admin/cleanup-rules/suggest returns a valid suggestion."""
    app, _ = make_test_app(tmp_path, monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/admin/cleanup-rules/suggest",
            json={
                "markdown_sample": "Some markdown\nwith issues",
                "issues": "headings inconsistent",
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "rule_yaml" in body
    assert "rationale" in body
    assert len(body["rule_yaml"]) > 0


@pytest.mark.asyncio
async def test_cleanup_rules_suggest_endpoint_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With empty payload, returns empty rule_yaml."""
    app, _ = make_test_app(tmp_path, monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/admin/cleanup-rules/suggest",
            json={"markdown_sample": "", "issues": ""},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["rule_yaml"] == ""
