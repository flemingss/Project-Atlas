from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

# Multimodal content: either a plain string or a list of content parts
# (e.g. [{"type": "text", "text": "..."}, {"type": "image_url", "image_url": {"url": "data:..."}}])
ContentPart = dict[str, Any]
MessageContent = str | list[ContentPart]


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: MessageContent


class ILlmProvider(Protocol):
    async def chat(self, *, model: str, messages: list[ChatMessage], params: dict[str, Any]) -> str: ...

    async def embed(self, *, model: str, texts: list[str], params: dict[str, Any]) -> list[list[float]]: ...
