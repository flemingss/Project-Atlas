"""Lightweight retry / exponential-backoff helpers.

No external dependencies — uses only stdlib asyncio + time.
Provides both sync and async decorators, plus a config dataclass
that is loaded from the ``retry`` section of ``pipeline.yaml``.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Type

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RetryConfig:
    """Retry knobs — one instance per call-site category."""

    max_retries: int = 3
    base_delay_s: float = 1.0
    max_delay_s: float = 30.0
    jitter: bool = True
    retryable_exceptions: tuple[Type[BaseException], ...] = (Exception,)

    def delay_for(self, attempt: int) -> float:
        """Exponential backoff with optional jitter."""
        d = min(self.base_delay_s * (2 ** attempt), self.max_delay_s)
        if self.jitter:
            d = d * (0.5 + random.random() * 0.5)  # noqa: S311
        return d


# Pre-built configs keyed by subsystem; overridden at startup from YAML.
_DEFAULT_CONFIGS: dict[str, RetryConfig] = {
    "llm": RetryConfig(max_retries=3, base_delay_s=2.0, max_delay_s=30.0),
    "vectorstore": RetryConfig(max_retries=3, base_delay_s=1.0, max_delay_s=15.0),
    "docling": RetryConfig(max_retries=2, base_delay_s=3.0, max_delay_s=30.0),
}

_configs: dict[str, RetryConfig] = dict(_DEFAULT_CONFIGS)


def get_retry_config(subsystem: str) -> RetryConfig:
    return _configs.get(subsystem, RetryConfig())


def load_retry_configs(raw: dict[str, Any] | None) -> None:
    """Load retry section from pipeline.yaml into the module-level registry.

    Expected YAML shape::

        retry:
          llm:
            max_retries: 3
            base_delay_s: 2.0
            max_delay_s: 30.0
          vectorstore:
            max_retries: 3
            base_delay_s: 1.0
            max_delay_s: 15.0
          docling:
            max_retries: 2
            base_delay_s: 3.0
            max_delay_s: 30.0
    """
    if not raw:
        return
    for key, defaults in _DEFAULT_CONFIGS.items():
        section = raw.get(key)
        if not section or not isinstance(section, dict):
            continue
        _configs[key] = RetryConfig(
            max_retries=int(section.get("max_retries", defaults.max_retries)),
            base_delay_s=float(section.get("base_delay_s", defaults.base_delay_s)),
            max_delay_s=float(section.get("max_delay_s", defaults.max_delay_s)),
            jitter=bool(section.get("jitter", defaults.jitter)),
            retryable_exceptions=defaults.retryable_exceptions,
        )


# ---------------------------------------------------------------------------
# Async retry wrapper
# ---------------------------------------------------------------------------

async def async_retry(
    fn: Callable[..., Any],
    *args: Any,
    config: RetryConfig | None = None,
    subsystem: str = "",
    operation: str = "",
    **kwargs: Any,
) -> Any:
    """Call *fn* with retry/backoff.  Returns the result or re-raises."""
    cfg = config or get_retry_config(subsystem)
    last_exc: BaseException | None = None

    for attempt in range(cfg.max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except cfg.retryable_exceptions as exc:
            last_exc = exc
            if attempt >= cfg.max_retries:
                break
            delay = cfg.delay_for(attempt)
            log.warning(
                "Retry %d/%d for %s.%s after %.1fs — %s: %s",
                attempt + 1,
                cfg.max_retries,
                subsystem or "unknown",
                operation or fn.__name__,
                delay,
                type(exc).__name__,
                exc,
            )
            await asyncio.sleep(delay)

    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# Sync retry wrapper
# ---------------------------------------------------------------------------

def sync_retry(
    fn: Callable[..., Any],
    *args: Any,
    config: RetryConfig | None = None,
    subsystem: str = "",
    operation: str = "",
    **kwargs: Any,
) -> Any:
    """Synchronous equivalent of :func:`async_retry`."""
    cfg = config or get_retry_config(subsystem)
    last_exc: BaseException | None = None

    for attempt in range(cfg.max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except cfg.retryable_exceptions as exc:
            last_exc = exc
            if attempt >= cfg.max_retries:
                break
            delay = cfg.delay_for(attempt)
            log.warning(
                "Retry %d/%d for %s.%s after %.1fs — %s: %s",
                attempt + 1,
                cfg.max_retries,
                subsystem or "unknown",
                operation or fn.__name__,
                delay,
                type(exc).__name__,
                exc,
            )
            time.sleep(delay)

    assert last_exc is not None
    raise last_exc
