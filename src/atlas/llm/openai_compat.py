from __future__ import annotations

from typing import Any

import httpx

from atlas.llm.provider import ChatMessage, ILlmProvider


class OpenAICompatibleProvider(ILlmProvider):
    def __init__(self, *, base_url: str, timeout_s: float = 120.0):
        self._base_url = base_url.rstrip("/")
        if self._base_url.endswith("/v1"):
            self._v1 = self._base_url
        else:
            self._v1 = f"{self._base_url}/v1"
        self._timeout = timeout_s

    async def chat(self, *, model: str, messages: list[ChatMessage], params: dict[str, Any]) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            **(params or {}),
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._v1}/chat/completions", json=payload)
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as e:
                    raise ValueError(
                        f"OpenAI-compatible chat failed ({resp.status_code}) for model='{model}' at '{self._v1}': {resp.text}"
                    ) from e
                data = resp.json()
        except httpx.RequestError as e:
            raise ValueError(
                "OpenAI-compatible chat request failed. "
                f"Is your server running and reachable at '{self._v1}'? "
                "If you are using LM Studio, ensure the server is started and ATLAS_OPENAI_BASE_URL points to it. "
                f"Original error: {e}"
            ) from e

        # OpenAI-style: choices[0].message.content
        try:
            return data["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"Unexpected chat response shape: {data}") from e

    async def embed(self, *, model: str, texts: list[str], params: dict[str, Any]) -> list[list[float]]:
        payload: dict[str, Any] = {
            "model": model,
            "input": texts,
            **(params or {}),
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._v1}/embeddings", json=payload)
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as e:
                    raise ValueError(
                        f"OpenAI-compatible embeddings failed ({resp.status_code}) for model='{model}' at '{self._v1}': {resp.text}"
                    ) from e
                data = resp.json()
        except httpx.RequestError as e:
            raise ValueError(
                "OpenAI-compatible embeddings request failed. "
                f"Is your server running and reachable at '{self._v1}'? "
                "If you are using LM Studio, ensure the server is started and ATLAS_OPENAI_BASE_URL points to it. "
                "Alternatively, you can switch the embed model provider to a deterministic local provider in config/models.yaml for dev. "
                f"Original error: {e}"
            ) from e

        try:
            return [row["embedding"] for row in data["data"]]
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"Unexpected embeddings response shape: {data}") from e
