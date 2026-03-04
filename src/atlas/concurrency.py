"""Concurrency and resource management for Project Atlas (HLD section 4).

Provides:
- vLLM semaphore for heavy task concurrency control
- VRAM monitoring and thresholds
- Queue depth tracking
- Frontier fallback logic
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from dataclasses import dataclass

from datetime import datetime, timezone

from atlas.diagnostics import get_diagnostics


@dataclass
class ResourceMetrics:
    """Current resource usage metrics."""

    vram_percent: float
    queue_depth: int
    active_heavy_tasks: int
    timestamp: str


class ConcurrencyGuard:
    """Concurrency guard with semaphore for heavy tasks (HLD section 4).

    Implements:
    - vLLM Semaphore: Concurrency = 1 for "Heavy" tasks
    - VRAM monitoring and fallback triggers
    - Queue depth tracking
    """

    def __init__(
        self,
        *,
        heavy_task_limit: int = 1,
        vram_threshold_percent: float = 92.0,
        queue_depth_threshold: int = 2,
    ):
        self.heavy_semaphore = asyncio.Semaphore(heavy_task_limit)
        self.heavy_task_limit = heavy_task_limit
        self.vram_threshold = vram_threshold_percent
        self.queue_depth_threshold = queue_depth_threshold
        self.queue_depth = 0
        self.active_tasks = 0  # Track active tasks explicitly
        self.logger = logging.getLogger("atlas.concurrency")
        self.diagnostics = get_diagnostics()

    async def acquire_heavy_task(self) -> None:
        """Acquire semaphore for a heavy task (e.g., local LLM inference)."""
        self.queue_depth += 1
        try:
            self.diagnostics.log_info(
                component="concurrency",
                message=f"Acquiring heavy task slot (queue_depth={self.queue_depth})",
            )
            await self.heavy_semaphore.acquire()
            self.active_tasks += 1
        finally:
            self.queue_depth -= 1

    def release_heavy_task(self) -> None:
        """Release semaphore for a heavy task."""
        self.heavy_semaphore.release()
        self.active_tasks -= 1
        self.diagnostics.log_info(
            component="concurrency",
            message="Released heavy task slot",
        )

    async def _get_vram_percent(self) -> float:
        """Return highest GPU VRAM usage (%) across all GPUs via nvidia-smi.

        Returns 0.0 when nvidia-smi is unavailable or times out so that
        resource checks degrade gracefully on CPU-only hosts.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader,nounits",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return 0.0
            if proc.returncode != 0:
                return 0.0  # nvidia-smi present but failed → assume OK
            max_percent = 0.0
            for line in stdout.decode().strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    used, total = map(float, line.split(","))
                    if total > 0:
                        max_percent = max(max_percent, (used / total) * 100.0)
                except ValueError:
                    continue  # skip malformed lines
            return max_percent
        except FileNotFoundError:
            return 0.0  # nvidia-smi not available → skip check

    async def check_resources(self) -> ResourceMetrics:
        """Check current resource usage, including GPU VRAM via nvidia-smi."""
        vram_percent = await self._get_vram_percent()
        metrics = ResourceMetrics(
            vram_percent=vram_percent,
            queue_depth=self.queue_depth,
            active_heavy_tasks=self.active_tasks,
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        return metrics

    async def should_fallback_to_frontier(self, *, frontier_enabled: bool) -> bool:
        """Determine if we should fallback to frontier API (HLD section 4).

        Returns True if:
        - Frontier fallback is enabled
        - VRAM exceeds threshold OR queue depth exceeds threshold
        """
        if not frontier_enabled:
            return False

        metrics = await self.check_resources()

        if metrics.vram_percent > self.vram_threshold:
            self.diagnostics.log_warning(
                component="concurrency",
                message=f"VRAM threshold exceeded: {metrics.vram_percent}% > {self.vram_threshold}%",
                context={"vram_percent": metrics.vram_percent},
            )
            return True

        if metrics.queue_depth > self.queue_depth_threshold:
            self.diagnostics.log_warning(
                component="concurrency",
                message=f"Queue depth exceeded: {metrics.queue_depth} > {self.queue_depth_threshold}",
                context={"queue_depth": metrics.queue_depth},
            )
            return True

        return False


class ResourceGuard:
    """Resource guard for managing model execution (HLD section 4).

    Implements privacy guard and resource-based routing.
    """

    def __init__(self, *, default_is_sensitive: bool = True):
        self.default_is_sensitive = default_is_sensitive
        self.logger = logging.getLogger("atlas.resource_guard")
        self.diagnostics = get_diagnostics()

    def check_privacy(self, *, is_sensitive: bool | None, allow_api: bool) -> bool:
        """Check if operation is allowed given privacy constraints (HLD section 4).

        Privacy Guard: Default is_sensitive: true. Override required for API routing.
        Enforced per-tenant.
        """
        sensitive = is_sensitive if is_sensitive is not None else self.default_is_sensitive

        if sensitive and allow_api:
            self.diagnostics.log_warning(
                component="resource_guard",
                message="Attempting API routing for sensitive data",
                context={"is_sensitive": sensitive, "allow_api": allow_api},
            )
            return False

        return True

    def select_provider(
        self,
        *,
        preferred_provider: str,
        fallback_provider: str,
        is_sensitive: bool | None,
        concurrency_guard: ConcurrencyGuard,
        frontier_enabled: bool,
    ) -> str:
        """Select provider based on resource constraints and privacy.

        Returns the provider to use based on:
        - Privacy constraints
        - Resource availability
        - Frontier fallback configuration
        """
        # Check privacy first
        can_use_api = self.check_privacy(
            is_sensitive=is_sensitive,
            allow_api=True,  # This should be per-tenant config
        )

        # If sensitive data, must use local
        if not can_use_api:
            return preferred_provider

        # Check if we should fallback due to resources
        # Note: This is async but we're in sync context - would need restructuring
        # For now, return preferred
        return preferred_provider


# Global concurrency guard instance
_global_concurrency_guard: ConcurrencyGuard | None = None


def get_concurrency_guard(
    *,
    heavy_task_limit: int = 1,
    vram_threshold: float = 92.0,
    queue_depth_threshold: int = 2,
) -> ConcurrencyGuard:
    """Get or create the global concurrency guard."""
    global _global_concurrency_guard
    if _global_concurrency_guard is None:
        _global_concurrency_guard = ConcurrencyGuard(
            heavy_task_limit=heavy_task_limit,
            vram_threshold_percent=vram_threshold,
            queue_depth_threshold=queue_depth_threshold,
        )
    return _global_concurrency_guard
