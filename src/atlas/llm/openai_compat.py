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

# Transport failures that are genuinely transient: the request never reached
# the model, so replaying it is free and likely to succeed.
_RETRYABLE_TRANSPORT = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.NetworkError,
)

# Read/write timeouts are NOT retryable. They mean the model accepted the
# request and was still working when we gave up — typically a holistic refine
# of a large document. Replaying it identically just burns another full
# timeout window (and, with max_retries=3, up to 4x the wall clock) before
# failing anyway. Raise immediately with a message that points at the real fix.
_NON_RETRYABLE_TIMEOUT = (httpx.ReadTimeout, httpx.WriteTimeout)


def _is_retryable_http(exc: BaseException) -> bool:
    """Return True for transient HTTP errors (429, 5xx) but not client 4xx."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    if isinstance(exc, _NON_RETRYABLE_TIMEOUT):
        return False
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
    """Client for any OpenAI-compatible /v1 endpoint.

    One implementation covers every generation and embedding surface Atlas
    talks to — LM Studio, an OpenRouter gateway, or the in-stack embedding
    sidecar — because they all speak the same wire protocol. Providers differ
    only in ``base_url``, credentials, and per-endpoint tuning, all supplied
    from ``models.yaml`` rather than hardcoded here.

    Timeouts are granular on purpose. A single flat timeout cannot serve both
    a sub-second embedding call and a holistic refine of a 100-page document:
    set it low and large refines die, set it high and a dead endpoint hangs
    the pipeline for the full window. Splitting connect from read lets a
    genuinely unreachable server fail in seconds while a legitimately slow
    generation runs for as long as it needs.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
        connect_timeout_s: float = 10.0,
        read_timeout_s: float = 120.0,
        write_timeout_s: float = 60.0,
        zdr_enforced: bool = False,
    ):
        self._base_url = base_url.rstrip("/")
        if self._base_url.endswith("/v1"):
            self._v1 = self._base_url
        else:
            self._v1 = f"{self._base_url}/v1"

        self._timeout = httpx.Timeout(
            connect=connect_timeout_s,
            read=read_timeout_s,
            write=write_timeout_s,
            pool=connect_timeout_s,
        )
        self._read_timeout_s = read_timeout_s

        # Extra keys merged into every request body. This is how per-request
        # ZDR enforcement (``provider: {zdr: true}``) reaches the wire without
        # the call sites needing to know the provider is OpenRouter.
        self._extra_body: dict[str, Any] = dict(extra_body or {})
        self._zdr_enforced = zdr_enforced

        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if extra_headers:
            headers.update(extra_headers)
        self._headers = headers

    # ------------------------------------------------------------------
    # Internal: error shaping
    # ------------------------------------------------------------------

    def _describe_http_failure(self, *, status_code: int, body: str, model: str, op: str) -> str:
        """Build an error message that names the likely cause, not just the code.

        When ZDR is enforced, routing is restricted to compliant endpoints.
        A model with no compliant provider fails here in a way that looks like
        a plain 'model not found', which sends you hunting for a typo in the
        model id. Say so explicitly instead.
        """
        msg = (
            f"OpenAI-compatible {op} failed ({status_code}) for model='{model}' "
            f"at '{self._v1}': {body}"
        )
        if self._zdr_enforced and status_code in (400, 404, 422):
            msg += (
                "\n\nNOTE: this provider enforces zero-data-retention routing "
                "(provider.zdr=true), which restricts the request to ZDR-compliant "
                f"endpoints. If '{model}' has no ZDR-compliant provider on the "
                "gateway, it will fail here even though the model id is valid. "
                "Verify ZDR availability for this model, or set enforce_zdr: false "
                "on this provider in models.yaml to fall back to account-level policy."
            )
        return msg

    def _timeout_message(self, *, model: str, op: str, exc: BaseException) -> str:
        return (
            f"OpenAI-compatible {op} timed out after {self._read_timeout_s:.0f}s "
            f"for model='{model}' at '{self._v1}'. The request was accepted and "
            "still generating when we gave up, so this is not retried. Either "
            "raise read_timeout_s for this provider in models.yaml, or reduce the "
            "work per call (lower max_context_tokens so large documents take the "
            f"sectional path instead of a single holistic pass). Original error: {exc}"
        )

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
            async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as client:
                resp = await client.post(f"{self._v1}/chat/completions", json=payload)
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as e:
                    if _is_retryable_http(e):
                        raise _LlmRetryableError(str(e)) from e
                    raise ValueError(
                        self._describe_http_failure(
                            status_code=resp.status_code,
                            body=resp.text,
                            model=model,
                            op="chat",
                        )
                    ) from e
                data = resp.json()
        except _NON_RETRYABLE_TIMEOUT as e:
            # Deliberately not wrapped in _LlmRetryableError — see the constant.
            raise ValueError(self._timeout_message(model=model, op="chat", exc=e)) from e
        except httpx.RequestError as e:
            raise _LlmRetryableError(
                "OpenAI-compatible chat request failed. "
                f"Is your server running and reachable at '{self._v1}'? "
                f"Original error: {e}"
            ) from e

        try:
            message = data["choices"][0]["message"]
            raw_content = message.get("content")
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"Unexpected chat response shape: {data}") from e

        # Token accounting. Gateways return a usage block, and OpenRouter adds
        # the actual charge for the call. Logging it here is the only place that
        # sees every request, so it is what turns "is this cost effective?" from
        # an estimate into a measurement. Reasoning models bill thinking tokens
        # as completion tokens, so a role whose completion count dwarfs its
        # visible output is paying for reasoning it does not need.
        usage = data.get("usage") or {}
        if usage:
            log.info(
                "LLM usage model=%s prompt=%s completion=%s total=%s cost=%s",
                payload.get("model", "unknown"),
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
                usage.get("total_tokens"),
                usage.get("cost", "n/a"),
            )

        # Log finish_reason — helps diagnose max_tokens truncation
        finish_reason = None
        try:
            finish_reason = data["choices"][0].get("finish_reason")
        except (KeyError, IndexError, TypeError):
            pass
        if finish_reason and finish_reason != "stop":
            log.warning(
                "LLM finish_reason=%s for model=%s (response truncated). For a "
                "refine call this means the rewrite is incomplete and will likely "
                "trip the section-preservation guard — lower max_context_tokens "
                "so oversized documents take the sectional path, or raise the "
                "role's max_output_tokens if the model allows it.",
                finish_reason,
                payload.get("model", "unknown"),
            )

        # A reasoning model can return content=null while still returning a
        # populated 'reasoning' field. This is not a malformed response — it
        # means the token budget was spent thinking and ran out before any
        # visible answer was emitted. It is easy to hit, because max_tokens on a
        # role is naturally sized for the answer alone while reasoning bills
        # against the same budget. Without this branch it surfaces from the
        # regex below as "expected string or bytes-like object, got 'NoneType'",
        # which says nothing about the actual cause or the fix.
        if raw_content is None:
            reasoning = message.get("reasoning") or ""
            budget = payload.get("max_tokens")
            completion_tokens = usage.get("completion_tokens")
            if finish_reason == "length":
                raise ValueError(
                    f"Model '{payload.get('model', 'unknown')}' returned no content: "
                    f"the max_tokens budget ({budget}) was exhausted before any "
                    f"visible output was produced ({completion_tokens} completion "
                    f"tokens spent, {len(reasoning)} chars of them returned as "
                    "reasoning). Reasoning models bill thinking against the same "
                    "budget and return it separately from content, so a limit "
                    "sized for the answer alone is too small. Raise max_tokens for "
                    "this role in models.yaml, or use a model whose reasoning can "
                    "be disabled."
                )
            raise ValueError(
                f"Model '{payload.get('model', 'unknown')}' returned null content "
                f"with finish_reason={finish_reason!r}. Full response: {data}"
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
            async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as client:
                resp = await client.post(f"{self._v1}/embeddings", json=payload)
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as e:
                    if _is_retryable_http(e):
                        raise _LlmRetryableError(str(e)) from e
                    raise ValueError(
                        self._describe_http_failure(
                            status_code=resp.status_code,
                            body=resp.text,
                            model=model,
                            op="embeddings",
                        )
                    ) from e
                data = resp.json()
        except _NON_RETRYABLE_TIMEOUT as e:
            # Deliberately not wrapped in _LlmRetryableError — see the constant.
            raise ValueError(self._timeout_message(model=model, op="embeddings", exc=e)) from e
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
        # Provider-level request keys (ZDR routing policy, etc). Applied after
        # params so a role cannot accidentally override a safety setting.
        if self._extra_body:
            payload.update(self._extra_body)
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

    # Embedding servers cap the number of inputs per request — the TEI sidecar
    # rejects anything above its --max-client-batch-size (default 32) with a
    # 422. Batch client-side so callers can embed arbitrarily many texts (a
    # single committed manual easily chunks past any server-side cap).
    _EMBED_MAX_BATCH = 32

    async def embed(self, *, model: str, texts: list[str], params: dict[str, Any]) -> list[list[float]]:
        cfg = get_retry_config("llm")
        retry_cfg = RetryConfig(
            max_retries=cfg.max_retries,
            base_delay_s=cfg.base_delay_s,
            max_delay_s=cfg.max_delay_s,
            jitter=cfg.jitter,
            retryable_exceptions=(_LlmRetryableError,),
        )

        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._EMBED_MAX_BATCH):
            batch = texts[start : start + self._EMBED_MAX_BATCH]
            payload: dict[str, Any] = {
                "model": model,
                "input": batch,
                **(params or {}),
            }
            # Provider-level request keys (ZDR routing policy, etc). Applied after
            # params so a role cannot accidentally override a safety setting.
            if self._extra_body:
                payload.update(self._extra_body)
            if os.environ.get("ATLAS_OPENAI_TRACE"):
                print(
                    f"[openai_compat] POST {self._v1}/embeddings model={model} "
                    f"n={len(batch)} ({start + len(batch)}/{len(texts)})",
                    file=sys.stderr,
                )
            try:
                vectors.extend(
                    await async_retry(
                        self._do_embed,
                        model=model,
                        payload=payload,
                        config=retry_cfg,
                        subsystem="llm",
                        operation="embed",
                    )
                )
            except _LlmRetryableError as e:
                raise ValueError(str(e)) from e
        return vectors
