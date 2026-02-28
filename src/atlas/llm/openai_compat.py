from __future__ import annotations

import logging
import re
from typing import Any

import os
import sys

import httpx

from atlas.llm.provider import ChatMessage, ILlmProvider
from atlas.retry import RetryConfig, async_retry, get_retry_config

log = logging.getLogger(__name__)

# Exceptions worth retrying — transient network / server errors.
_RETRYABLE = (httpx.RequestError, httpx.HTTPStatusError)


def _is_retryable_http(exc: BaseException) -> bool:
    """Return True for transient HTTP errors (429, 5xx) but not client 4xx."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.RequestError)


# Pattern to match <think>...</think> blocks emitted by reasoning models
# (Qwen3, DeepSeek-R1, etc.).  Uses DOTALL so '.' matches newlines.
_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_reasoning_tags(text: str) -> str:
    """Remove ``<think>…</think>`` reasoning blocks from model output.

    Reasoning models (Qwen3, DeepSeek-R1, QwQ) emit chain-of-thought
    inside ``<think>`` tags by default.  These blocks consume the
    ``max_tokens`` budget and corrupt downstream consumers that expect
    clean markdown or structured output.

    Returns the text with all ``<think>`` blocks removed and leading
    whitespace stripped.
    """
    cleaned = _THINK_TAG_RE.sub("", text)
    return cleaned.strip()


class _LlmRetryableError(Exception):
    """Wrapper so the retry loop can catch a single type."""


class OpenAICompatibleProvider(ILlmProvider):
    def __init__(self, *, base_url: str, timeout_s: float = 120.0):
        self._base_url = base_url.rstrip("/")
        if self._base_url.endswith("/v1"):
            self._v1 = self._base_url
        else:
            self._v1 = f"{self._base_url}/v1"
        self._timeout = timeout_s

    # ------------------------------------------------------------------
    # Internal: single-attempt request helpers
    # ------------------------------------------------------------------

    async def _do_chat(self, *, model: str, payload: dict[str, Any]) -> str:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._v1}/chat/completions", json=payload)
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as e:
                    if _is_retryable_http(e):
                        raise _LlmRetryableError(str(e)) from e
                    raise ValueError(
                        f"OpenAI-compatible chat failed ({resp.status_code}) for model='{model}' at '{self._v1}': {resp.text}"
                    ) from e
                data = resp.json()
        except httpx.RequestError as e:
            raise _LlmRetryableError(
                "OpenAI-compatible chat request failed. "
                f"Is your server running and reachable at '{self._v1}'? "
                f"Original error: {e}"
            ) from e

        try:
            raw_content = data["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"Unexpected chat response shape: {data}") from e

        # Strip <think> reasoning blocks from models like Qwen3 / DeepSeek-R1
        cleaned = strip_reasoning_tags(raw_content)
        if len(cleaned) != len(raw_content):
            stripped_chars = len(raw_content) - len(cleaned)
            log.info(
                "Stripped %d chars of <think> reasoning from %s response",
                stripped_chars,
                payload.get("model", "unknown"),
            )
        return cleaned

    async def _do_embed(self, *, model: str, payload: dict[str, Any]) -> list[list[float]]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._v1}/embeddings", json=payload)
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as e:
                    if _is_retryable_http(e):
                        raise _LlmRetryableError(str(e)) from e
                    raise ValueError(
                        f"OpenAI-compatible embeddings failed ({resp.status_code}) for model='{model}' at '{self._v1}': {resp.text}"
                    ) from e
                data = resp.json()
        except httpx.RequestError as e:
            raise _LlmRetryableError(
                "OpenAI-compatible embeddings request failed. "
                f"Is your server running and reachable at '{self._v1}'? "
                f"Original error: {e}"
            ) from e

        try:
            return [row["embedding"] for row in data["data"]]
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"Unexpected embeddings response shape: {data}") from e

    # ------------------------------------------------------------------
    # Public API — with retry
    # ------------------------------------------------------------------

    async def chat(self, *, model: str, messages: list[ChatMessage], params: dict[str, Any]) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            **(params or {}),
        }
        if os.environ.get("ATLAS_OPENAI_TRACE"):
            print(f"[openai_compat] POST {self._v1}/chat/completions model={model}", file=sys.stderr)

        cfg = get_retry_config("llm")
        retry_cfg = RetryConfig(
            max_retries=cfg.max_retries,
            base_delay_s=cfg.base_delay_s,
            max_delay_s=cfg.max_delay_s,
            jitter=cfg.jitter,
            retryable_exceptions=(_LlmRetryableError,),
        )
        try:
            return await async_retry(
                self._do_chat,
                model=model,
                payload=payload,
                config=retry_cfg,
                subsystem="llm",
                operation="chat",
            )
        except _LlmRetryableError as e:
            raise ValueError(str(e)) from e

    async def embed(self, *, model: str, texts: list[str], params: dict[str, Any]) -> list[list[float]]:
        payload: dict[str, Any] = {
            "model": model,
            "input": texts,
            **(params or {}),
        }
        if os.environ.get("ATLAS_OPENAI_TRACE"):
            print(f"[openai_compat] POST {self._v1}/embeddings model={model} n={len(texts)}", file=sys.stderr)

        cfg = get_retry_config("llm")
        retry_cfg = RetryConfig(
            max_retries=cfg.max_retries,
            base_delay_s=cfg.base_delay_s,
            max_delay_s=cfg.max_delay_s,
            jitter=cfg.jitter,
            retryable_exceptions=(_LlmRetryableError,),
        )
        try:
            return await async_retry(
                self._do_embed,
                model=model,
                payload=payload,
                config=retry_cfg,
                subsystem="llm",
                operation="embed",
            )
        except _LlmRetryableError as e:
            raise ValueError(str(e)) from e
