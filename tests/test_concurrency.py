"""Unit tests for atlas.concurrency.ConcurrencyGuard and ResourceGuard.

All tests are async (pytest-asyncio with asyncio_mode=auto from pyproject.toml).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.concurrency import ConcurrencyGuard, ResourceGuard, get_concurrency_guard


async def test_concurrency_guard_acquire_release() -> None:
    """Acquire then release should leave active_tasks == 0."""
    guard = ConcurrencyGuard(heavy_task_limit=2)
    await guard.acquire_heavy_task()
    assert guard.active_tasks == 1
    guard.release_heavy_task()
    assert guard.active_tasks == 0


async def test_concurrency_guard_semaphore_blocks() -> None:
    """With heavy_task_limit=1, a second acquire should block (timeout verifies this)."""
    guard = ConcurrencyGuard(heavy_task_limit=1)
    await guard.acquire_heavy_task()
    assert guard.active_tasks == 1

    # A second acquire must block — asyncio.wait_for should time out.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(guard.acquire_heavy_task(), timeout=0.05)

    guard.release_heavy_task()


async def test_concurrency_guard_queue_depth_tracking() -> None:
    """Queue depth increments while waiting and decrements after acquire."""
    guard = ConcurrencyGuard(heavy_task_limit=1)
    # Occupy the single slot.
    await guard.acquire_heavy_task()

    # Start a second acquire in the background — it should sit in the queue.
    task = asyncio.create_task(guard.acquire_heavy_task())

    # Wait (with polling) until the background task has incremented queue_depth.
    for _ in range(100):
        if guard.queue_depth > 0:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("queue_depth did not increase while task was queued")

    # Release the first slot — the waiting task should proceed.
    guard.release_heavy_task()
    await asyncio.wait_for(task, timeout=1.0)
    # After the queued task acquires, there should be no queued tasks.
    assert guard.queue_depth == 0
    guard.release_heavy_task()
    assert guard.active_tasks == 0


def test_resource_guard_privacy_check() -> None:
    """Sensitive data must not be routed via API; local routing remains allowed."""
    rg = ResourceGuard()
    # Sensitive data + API routing requested → must be rejected
    result = rg.check_privacy(is_sensitive=True, allow_api=True)
    assert result is False

    # Non-sensitive data + API routing is allowed
    result = rg.check_privacy(is_sensitive=False, allow_api=True)
    assert result is True

    # Sensitive data, but API not requested → local routing is fine
    result = rg.check_privacy(is_sensitive=True, allow_api=False)
    assert result is True


def test_get_concurrency_guard_singleton() -> None:
    """Two calls to get_concurrency_guard() should return the same instance."""
    import atlas.concurrency as _concurrency_mod

    # Reset the global guard to ensure a clean state for this test.
    _concurrency_mod._global_concurrency_guard = None

    g1 = get_concurrency_guard()
    g2 = get_concurrency_guard()
    assert g1 is g2


# ---------------------------------------------------------------------------
# VRAM monitoring tests
# ---------------------------------------------------------------------------


def _make_nvidia_proc(stdout_text: str, returncode: int = 0) -> MagicMock:
    """Return a mock async subprocess whose communicate() yields the given output."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout_text.encode(), b""))
    proc.kill = MagicMock()
    return proc


async def test_check_resources_normal() -> None:
    """nvidia-smi returns valid output below threshold → vram_percent is computed."""
    guard = ConcurrencyGuard(vram_threshold_percent=92.0)
    proc = _make_nvidia_proc("8000, 16000\n")

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        metrics = await guard.check_resources()

    assert metrics.vram_percent == pytest.approx(50.0)


async def test_check_resources_threshold_exceeded() -> None:
    """VRAM above threshold causes should_fallback_to_frontier to return True."""
    guard = ConcurrencyGuard(vram_threshold_percent=80.0)
    proc = _make_nvidia_proc("14000, 16000\n")  # 87.5 % > 80 %

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        fallback = await guard.should_fallback_to_frontier(frontier_enabled=True)

    assert fallback is True


async def test_check_resources_nvidia_smi_not_found() -> None:
    """FileNotFoundError (no nvidia-smi) → vram_percent == 0.0, no exception."""
    guard = ConcurrencyGuard(vram_threshold_percent=92.0)

    with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
        metrics = await guard.check_resources()

    assert metrics.vram_percent == 0.0


async def test_check_resources_timeout() -> None:
    """asyncio.TimeoutError during communicate() → vram_percent == 0.0, no exception."""
    guard = ConcurrencyGuard(vram_threshold_percent=92.0)
    proc = MagicMock()
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
    proc.kill = MagicMock()
    proc.communicate = AsyncMock(side_effect=[asyncio.TimeoutError, (b"", b"")])

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        metrics = await guard.check_resources()

    assert metrics.vram_percent == 0.0
