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
# Handles both closed (<think>...</think>) and unclosed (<think>... EOF)
# tags — Qwen3 output is frequently truncated mid-thought when max_tokens
# is exhausted.
_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_THINK_TAG_UNCLOSED_RE = re.compile(r"<think>(?:(?!</think>).)*$", re.DOTALL)

# Cache for dynamically compiled tag regexes (keyed by tag name).
_TAG_RE_CACHE: dict[str, tuple[re.Pattern[str], re.Pattern[str]]] = {}


def _get_tag_patterns(tag: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    """Return compiled (closed, unclosed) regex pair for a given tag name."""
    if tag not in _TAG_RE_CACHE:
        # tag is e.g. "think" or "reasoning" — inner content of angle brackets
        closed = re.compile(rf"<{tag}>.*?</{tag}>", re.DOTALL)
        unclosed = re.compile(rf"<{tag}>(?:(?!</{tag}>).)*$", re.DOTALL)
        _TAG_RE_CACHE[tag] = (closed, unclosed)
    return _TAG_RE_CACHE[tag]


def strip_reasoning_tags(text: str, *, tag: str = "think") -> str:
    """Remove reasoning blocks wrapped in ``<tag>…</tag>`` from model output.

    Reasoning models (Qwen3, DeepSeek-R1, QwQ) emit chain-of-thought
    inside ``<think>`` tags by default.  These blocks consume the
    ``max_tokens`` budget and corrupt downstream consumers that expect
    clean markdown or structured output.

    Parameters
    ----------
    text:
        Raw model output.
    tag:
        The XML tag name to strip (default ``"think"``).  Pass e.g.
        ``"reasoning"`` for models that use ``<reasoning>…</reasoning>``.

    Handles both properly closed ``<tag>…</tag>`` blocks and
    unclosed ``<tag>…`` blocks (from max_tokens truncation).

    Returns the text with all reasoning blocks removed and leading
    whitespace stripped.
    """
    closed_re, unclosed_re = _get_tag_patterns(tag)
    cleaned = closed_re.sub("", text)
    cleaned = unclosed_re.sub("", cleaned)
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
        # Extract think_tag before sending — it's an Atlas-level param,
        # not part of the OpenAI API spec.
        think_tag: str | None = payload.pop("think_tag", None)
        # Derive the inner tag name (e.g. "think" from "<think>")
        tag_name: str = "think"  # default fallback for safety-net stripping
        if think_tag:
            tag_name = think_tag.strip("<>/ ")

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

        # Log finish_reason — helps diagnose max_tokens truncation
        finish_reason = None
        try:
            finish_reason = data["choices"][0].get("finish_reason")
        except (KeyError, IndexError, TypeError):
            pass
        if finish_reason and finish_reason != "stop":
            log.warning(
                "LLM finish_reason=%s for model=%s (may indicate truncation)",
                finish_reason,
                payload.get("model", "unknown"),
            )

        # Strip reasoning blocks using the configured tag (or default <think>).
        cleaned = strip_reasoning_tags(raw_content, tag=tag_name)
        if len(cleaned) != len(raw_content):
            stripped_chars = len(raw_content) - len(cleaned)
            # Log the thinking content at DEBUG for diagnostics
            if log.isEnabledFor(logging.DEBUG):
                thinking_content = raw_content[:len(raw_content) - len(cleaned)]
                log.debug(
                    "Thinking content (%d chars) from %s: %.500s%s",
                    stripped_chars,
                    payload.get("model", "unknown"),
                    thinking_content,
                    "..." if len(thinking_content) > 500 else "",
                )
            log.info(
                "Stripped %d chars of <%s> reasoning from %s response",
                stripped_chars,
                tag_name,
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
        # Serialize messages — content may be str or list[dict] (multimodal)
        serialized_messages = []
        for m in messages:
            serialized_messages.append({"role": m.role, "content": m.content})
        payload: dict[str, Any] = {
            "model": model,
            "messages": serialized_messages,
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
