from __future__ import annotations

import pytest

from atlas.llm.openai_compat import OpenAICompatibleProvider, strip_reasoning_tags
from atlas.llm.registry import ModelRegistry
from atlas.settings import Settings


# ---------------------------------------------------------------------------
# strip_reasoning_tags tests
# ---------------------------------------------------------------------------

def test_strip_reasoning_tags_removes_think_block() -> None:
    text = "<think>\nI need to fix the OCR errors...\n</think>\n# Heading\n\nContent."
    assert strip_reasoning_tags(text) == "# Heading\n\nContent."


def test_strip_reasoning_tags_removes_multiple_blocks() -> None:
    text = "<think>first</think>Hello <think>second</think>World"
    assert strip_reasoning_tags(text) == "Hello World"


def test_strip_reasoning_tags_handles_multiline() -> None:
    text = (
        "<think>\nOkay, let me think step by step.\n"
        "1. First I need to check...\n"
        "2. Then I should...\n"
        "</think>\n\n## Section 1\n\nReal content here."
    )
    result = strip_reasoning_tags(text)
    assert "<think>" not in result
    assert "## Section 1" in result
    assert "Real content here." in result


def test_strip_reasoning_tags_noop_without_tags() -> None:
    text = "# Normal Document\n\nNo reasoning tags here."
    assert strip_reasoning_tags(text) == text


def test_strip_reasoning_tags_empty_think_block() -> None:
    text = "<think></think>Content after."
    assert strip_reasoning_tags(text) == "Content after."


def test_strip_reasoning_tags_returns_empty_for_think_only() -> None:
    text = "<think>Only reasoning, no content.</think>"
    assert strip_reasoning_tags(text) == ""


def test_strip_reasoning_tags_preserves_angle_brackets_in_content() -> None:
    """Ensure <tag> in normal content is not stripped (only <think> pairs)."""
    text = "Use <strong>bold</strong> and <em>italic</em> tags."
    assert strip_reasoning_tags(text) == text


# ---------------------------------------------------------------------------
# ModelRegistry tests
# ---------------------------------------------------------------------------

def test_model_registry_resolve_and_provider_for_openai_compat() -> None:
    settings = Settings()
    models_cfg = {
        "providers": {"lmstudio": {"type": "openai_compat"}},
        "roles": {"embed_model": {"provider": "lmstudio", "model_name": "embed", "params": {}}},
    }
    reg = ModelRegistry(settings=settings, models_cfg=models_cfg)

    resolved = reg.resolve("embed_model")
    assert resolved.provider_name == "lmstudio"
    assert resolved.model_name == "embed"

    provider = reg.provider_for(resolved.provider_name)
    assert isinstance(provider, OpenAICompatibleProvider)


def test_model_registry_unknown_role_raises() -> None:
    reg = ModelRegistry(settings=Settings(), models_cfg={"providers": {}, "roles": {}})
    with pytest.raises(KeyError):
        reg.resolve("missing")
