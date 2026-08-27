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


def test_strip_reasoning_tags_custom_tag() -> None:
    """Configurable tag= parameter strips different reasoning delimiters."""
    text = "<reasoning>\nStep 1: Analysis...\n</reasoning>\n# Result\n\nFinal answer."
    assert strip_reasoning_tags(text, tag="reasoning") == "# Result\n\nFinal answer."


def test_strip_reasoning_tags_custom_tag_unclosed() -> None:
    """Unclosed custom tag is also stripped (max_tokens truncation)."""
    text = "<reasoning>\nI'm still thinking about this..."
    assert strip_reasoning_tags(text, tag="reasoning") == ""


def test_strip_reasoning_tags_custom_tag_noop_on_different_tag() -> None:
    """Stripping with tag='reasoning' does NOT strip <think> blocks."""
    text = "<think>secret</think>Content"
    assert strip_reasoning_tags(text, tag="reasoning") == "<think>secret</think>Content"


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
    assert resolved.think_tag is None
    assert "think_tag" not in resolved.params

    provider = reg.provider_for(resolved.provider_name)
    assert isinstance(provider, OpenAICompatibleProvider)


def test_model_registry_resolve_with_think_tag() -> None:
    """When think_tag is set in the role config it appears in both ResolvedModel and params."""
    settings = Settings()
    models_cfg = {
        "providers": {"lmstudio": {"type": "openai_compat"}},
        "roles": {
            "judge_model": {
                "provider": "lmstudio",
                "model_name": "qwen3-14b",
                "think_tag": "<think>",
                "params": {"temperature": 0.0, "max_tokens": 500},
            }
        },
    }
    reg = ModelRegistry(settings=settings, models_cfg=models_cfg)
    resolved = reg.resolve("judge_model")

    assert resolved.think_tag == "<think>"
    assert resolved.params["think_tag"] == "<think>"
    # Original params are preserved
    assert resolved.params["temperature"] == 0.0
    assert resolved.params["max_tokens"] == 500


def test_model_registry_resolve_without_think_tag_no_injection() -> None:
    """When think_tag is absent in config, params dict is clean."""
    settings = Settings()
    models_cfg = {
        "providers": {"lmstudio": {"type": "openai_compat"}},
        "roles": {
            "embed_model": {
                "provider": "lmstudio",
                "model_name": "nomic",
                "params": {},
            }
        },
    }
    reg = ModelRegistry(settings=settings, models_cfg=models_cfg)
    resolved = reg.resolve("embed_model")

    assert resolved.think_tag is None
    assert "think_tag" not in resolved.params


def test_model_registry_unknown_role_raises() -> None:
    reg = ModelRegistry(settings=Settings(), models_cfg={"providers": {}, "roles": {}})
    with pytest.raises(KeyError):
        reg.resolve("missing")
