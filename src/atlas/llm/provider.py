from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


class ILlmProvider(Protocol):
    async def chat(self, *, model: str, messages: list[ChatMessage], params: dict[str, Any]) -> str: ...

    async def embed(self, *, model: str, texts: list[str], params: dict[str, Any]) -> list[list[float]]: ...
