"""Tests for atlas.retry — retry / backoff helpers."""

from __future__ import annotations

import pytest

from atlas.retry import (
    RetryConfig,
    async_retry,
    get_retry_config,
    load_retry_configs,
    sync_retry,
)

# ---------------------------------------------------------------------------
# RetryConfig.delay_for
# ---------------------------------------------------------------------------

def test_delay_for_exponential_no_jitter() -> None:
    cfg = RetryConfig(base_delay_s=1.0, max_delay_s=100.0, jitter=False)
    assert cfg.delay_for(0) == 1.0
    assert cfg.delay_for(1) == 2.0
    assert cfg.delay_for(2) == 4.0
    assert cfg.delay_for(3) == 8.0


def test_delay_for_caps_at_max() -> None:
    cfg = RetryConfig(base_delay_s=1.0, max_delay_s=5.0, jitter=False)
    assert cfg.delay_for(10) == 5.0


def test_delay_for_with_jitter() -> None:
    cfg = RetryConfig(base_delay_s=1.0, max_delay_s=100.0, jitter=True)
    # With jitter, delay is in [0.5 * base * 2^attempt, base * 2^attempt].
    d = cfg.delay_for(0)
    assert 0.5 <= d <= 1.0


# ---------------------------------------------------------------------------
# load_retry_configs / get_retry_config
# ---------------------------------------------------------------------------

def test_default_configs_exist() -> None:
    for key in ("llm", "vectorstore", "docling"):
        cfg = get_retry_config(key)
        assert cfg.max_retries > 0


def test_load_retry_configs_overrides_defaults() -> None:
    load_retry_configs(
        {
            "llm": {"max_retries": 7, "base_delay_s": 0.5, "max_delay_s": 10.0},
        }
    )
    cfg = get_retry_config("llm")
    assert cfg.max_retries == 7
    assert cfg.base_delay_s == 0.5
    assert cfg.max_delay_s == 10.0
    # Restore defaults.
    load_retry_configs(None)


def test_load_retry_configs_ignores_missing_keys() -> None:
    old = get_retry_config("vectorstore")
    load_retry_configs({"llm": {"max_retries": 1}})
    # vectorstore should remain unchanged.
    assert get_retry_config("vectorstore").max_retries == old.max_retries
    load_retry_configs(None)


# ---------------------------------------------------------------------------
# sync_retry
# ---------------------------------------------------------------------------

def test_sync_retry_succeeds_first_attempt() -> None:
    calls: list[int] = []

    def fn() -> str:
        calls.append(1)
        return "ok"

    result = sync_retry(fn, config=RetryConfig(max_retries=3, retryable_exceptions=(ValueError,)))
    assert result == "ok"
    assert len(calls) == 1


def test_sync_retry_retries_then_succeeds() -> None:
    counter = {"n": 0}

    def fn() -> str:
        counter["n"] += 1
        if counter["n"] < 3:
            raise ValueError("transient")
        return "recovered"

    cfg = RetryConfig(max_retries=3, base_delay_s=0.01, max_delay_s=0.05, retryable_exceptions=(ValueError,))
    result = sync_retry(fn, config=cfg, subsystem="test", operation="retry_test")
    assert result == "recovered"
    assert counter["n"] == 3


def test_sync_retry_exhausts_retries() -> None:
    def fn() -> None:
        raise ValueError("permanent")

    cfg = RetryConfig(max_retries=2, base_delay_s=0.01, max_delay_s=0.05, retryable_exceptions=(ValueError,))
    with pytest.raises(ValueError, match="permanent"):
        sync_retry(fn, config=cfg)


def test_sync_retry_does_not_catch_non_retryable() -> None:
    def fn() -> None:
        raise TypeError("not retryable")

    cfg = RetryConfig(max_retries=3, base_delay_s=0.01, retryable_exceptions=(ValueError,))
    with pytest.raises(TypeError, match="not retryable"):
        sync_retry(fn, config=cfg)


# ---------------------------------------------------------------------------
# async_retry
# ---------------------------------------------------------------------------

async def test_async_retry_succeeds_first_attempt() -> None:
    calls: list[int] = []

    async def fn() -> str:
        calls.append(1)
        return "ok"

    result = await async_retry(fn, config=RetryConfig(max_retries=3, retryable_exceptions=(ValueError,)))
    assert result == "ok"
    assert len(calls) == 1


async def test_async_retry_retries_then_succeeds() -> None:
    counter = {"n": 0}

    async def fn() -> str:
        counter["n"] += 1
        if counter["n"] < 3:
            raise ValueError("transient")
        return "recovered"

    cfg = RetryConfig(max_retries=3, base_delay_s=0.01, max_delay_s=0.05, retryable_exceptions=(ValueError,))
    result = await async_retry(fn, config=cfg, subsystem="test", operation="async_test")
    assert result == "recovered"
    assert counter["n"] == 3


async def test_async_retry_exhausts_retries() -> None:
    async def fn() -> None:
        raise ValueError("permanent")

    cfg = RetryConfig(max_retries=2, base_delay_s=0.01, max_delay_s=0.05, retryable_exceptions=(ValueError,))
    with pytest.raises(ValueError, match="permanent"):
        await async_retry(fn, config=cfg)


async def test_async_retry_does_not_catch_non_retryable() -> None:
    async def fn() -> None:
        raise TypeError("not retryable")

    cfg = RetryConfig(max_retries=3, base_delay_s=0.01, retryable_exceptions=(ValueError,))
    with pytest.raises(TypeError, match="not retryable"):
        await async_retry(fn, config=cfg)
