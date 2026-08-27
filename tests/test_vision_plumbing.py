"""Tests for multimodal ChatMessage and improved think-tag stripping."""
from __future__ import annotations

import pytest

from atlas.llm.openai_compat import strip_reasoning_tags
from atlas.llm.provider import ChatMessage

# ===================================================================
# ChatMessage — multimodal content support
# ===================================================================


class TestChatMessageMultimodal:
    """Verify ChatMessage.content accepts both str and list[dict]."""

    def test_string_content(self) -> None:
        msg = ChatMessage(role="user", content="Hello")
        assert msg.content == "Hello"
        assert msg.role == "user"

    def test_list_content(self) -> None:
        content = [
            {"type": "text", "text": "Describe this image"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ]
        msg = ChatMessage(role="user", content=content)
        assert isinstance(msg.content, list)
        assert len(msg.content) == 2
        assert msg.content[0]["type"] == "text"
        assert msg.content[1]["type"] == "image_url"

    def test_frozen(self) -> None:
        msg = ChatMessage(role="user", content="test")
        with pytest.raises(AttributeError):
            msg.content = "changed"  # type: ignore[misc]

    def test_system_message_string(self) -> None:
        msg = ChatMessage(role="system", content="You are a helpful assistant.")
        assert msg.role == "system"
        assert isinstance(msg.content, str)

    def test_multimodal_with_multiple_images(self) -> None:
        content = [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,page1"}},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,page2"}},
            {"type": "text", "text": "Compare these two pages."},
        ]
        msg = ChatMessage(role="user", content=content)
        assert len(msg.content) == 3

    def test_serialization_string(self) -> None:
        """Content serializes correctly for API payload."""
        msg = ChatMessage(role="user", content="plain text")
        payload = {"role": msg.role, "content": msg.content}
        assert payload == {"role": "user", "content": "plain text"}

    def test_serialization_multimodal(self) -> None:
        """Multimodal content passes through as-is for API payload."""
        content = [{"type": "text", "text": "hello"}]
        msg = ChatMessage(role="user", content=content)
        payload = {"role": msg.role, "content": msg.content}
        assert isinstance(payload["content"], list)
        assert payload["content"][0]["type"] == "text"


# ===================================================================
# strip_reasoning_tags — improved regex
# ===================================================================


class TestStripReasoningTagsImproved:
    """Tests for the improved think-tag stripping that handles unclosed tags."""

    def test_closed_tag(self) -> None:
        text = "<think>reasoning here</think>Answer text"
        assert strip_reasoning_tags(text) == "Answer text"

    def test_multiple_closed_tags(self) -> None:
        text = "<think>first</think>middle<think>second</think>end"
        assert strip_reasoning_tags(text) == "middleend"

    def test_multiline_closed_tag(self) -> None:
        text = "<think>\nlet me think\nabout this\n</think>\n# Result"
        assert strip_reasoning_tags(text) == "# Result"

    def test_unclosed_tag_at_end(self) -> None:
        """Qwen3 truncation: max_tokens exhausted mid-thought."""
        text = "# Heading\n\n<think>\nI need to consider the following:\n1. Point A\n2. Point B"
        result = strip_reasoning_tags(text)
        assert result == "# Heading"

    def test_unclosed_tag_only(self) -> None:
        text = "<think>this is all reasoning with no output"
        result = strip_reasoning_tags(text)
        assert result == ""

    def test_closed_then_unclosed(self) -> None:
        """First tag closed, second truncated."""
        text = "<think>closed</think>Content<think>unclosed reasoning"
        result = strip_reasoning_tags(text)
        assert result == "Content"

    def test_no_tags(self) -> None:
        text = "Plain text with no think tags"
        assert strip_reasoning_tags(text) == "Plain text with no think tags"

    def test_empty_string(self) -> None:
        assert strip_reasoning_tags("") == ""

    def test_empty_think_tags(self) -> None:
        text = "<think></think>Output"
        assert strip_reasoning_tags(text) == "Output"

    def test_nested_angle_brackets_in_think(self) -> None:
        """Think block containing HTML-like content."""
        text = "<think>The user wants <b>bold</b> formatting</think>Result"
        assert strip_reasoning_tags(text) == "Result"

    def test_whitespace_stripping(self) -> None:
        text = "  \n<think>reasoning</think>\n  Result  "
        assert strip_reasoning_tags(text) == "Result"

    def test_unclosed_with_newlines(self) -> None:
        """Realistic truncation scenario with many newlines."""
        text = (
            "# Document Title\n\n"
            "Content here.\n\n"
            "<think>\n"
            "Let me analyze this document:\n"
            "- Section 1 has issues with formatting\n"
            "- Section 2 needs table fixes\n"
            "- I should focus on"
        )
        result = strip_reasoning_tags(text)
        assert result == "# Document Title\n\nContent here."

    def test_real_world_qwen3_truncation(self) -> None:
        """Simulates actual Qwen3 output with truncated reasoning."""
        text = (
            "<think>\n"
            "The user wants me to fix the markdown. Let me:\n"
            "1. Check the headings\n"
            "2. Fix table alignment\n"
            "3. Remove OCR artif"  # truncated mid-word
        )
        result = strip_reasoning_tags(text)
        assert result == ""

    def test_content_before_unclosed_preserved(self) -> None:
        """Valid content before an unclosed think tag is preserved."""
        text = "# Valid Heading\n\nParagraph content.\n\n<think>\nReasoning that got cut off"
        result = strip_reasoning_tags(text)
        assert "# Valid Heading" in result
        assert "Paragraph content." in result
        assert "<think>" not in result
