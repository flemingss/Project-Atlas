"""Diagnostics and structured error handling for Project Atlas (HLD section 5).

Provides structured error codes, trace-level logging, and performance metrics
for comprehensive diagnosability.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterator


def _utc_now_iso_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ErrorCode(str, Enum):
    """Structured error codes (HLD section 5: Diagnostics)."""

    # Ingest errors
    DOC_PARSE_TIMEOUT = "DOC_PARSE_TIMEOUT"
    DOC_PARSE_FAILED = "DOC_PARSE_FAILED"
    DOC_PARSE_DEPENDENCY_MISSING = "DOC_PARSE_DEPENDENCY_MISSING"
    DOC_OCR_EMPTY = "DOC_OCR_EMPTY"
    DOC_SIZE_LIMIT_EXCEEDED = "DOC_SIZE_LIMIT_EXCEEDED"
    DOC_PAGE_LIMIT_EXCEEDED = "DOC_PAGE_LIMIT_EXCEEDED"
    DOC_EXTRACT_LOW_QUALITY = "DOC_EXTRACT_LOW_QUALITY"
    DOC_LAYOUT_MODEL_UNAVAILABLE = "DOC_LAYOUT_MODEL_UNAVAILABLE"
    DOC_TABLE_EXTRACTION_FAILED = "DOC_TABLE_EXTRACTION_FAILED"
    DOC_OCR_CONFIDENCE_LOW = "DOC_OCR_CONFIDENCE_LOW"
    INVALID_MIME_TYPE = "INVALID_MIME_TYPE"

    # Judge errors
    JUDGE_MODEL_UNAVAILABLE = "JUDGE_MODEL_UNAVAILABLE"
    JUDGE_INVALID_SCORE = "JUDGE_INVALID_SCORE"

    # Refine errors
    VLM_OCR_FAIL = "VLM_OCR_FAIL"
    REFINE_MAX_RETRIES = "REFINE_MAX_RETRIES"
    REFINE_MODEL_ERROR = "REFINE_MODEL_ERROR"

    # Embedding errors
    EMBED_MODEL_UNAVAILABLE = "EMBED_MODEL_UNAVAILABLE"
    EMBED_DIMENSION_MISMATCH = "EMBED_DIMENSION_MISMATCH"

    # Vector store errors
    VECTORSTORE_UPSERT_FAILED = "VECTORSTORE_UPSERT_FAILED"
    VECTORSTORE_SEARCH_FAILED = "VECTORSTORE_SEARCH_FAILED"
    VECTORSTORE_CONNECTION_ERROR = "VECTORSTORE_CONNECTION_ERROR"

    # Resource errors
    VRAM_EXCEEDED = "VRAM_EXCEEDED"
    QUEUE_DEPTH_EXCEEDED = "QUEUE_DEPTH_EXCEEDED"
    CONCURRENCY_LIMIT = "CONCURRENCY_LIMIT"

    # Configuration errors
    CONFIG_INVALID = "CONFIG_INVALID"
    CONFIG_VERSION_NOT_FOUND = "CONFIG_VERSION_NOT_FOUND"

    # Pipeline errors
    PIPELINE_FAILED = "PIPELINE_FAILED"
    PIPELINE_INVALID_TRANSITION = "PIPELINE_INVALID_TRANSITION"
    INGEST_FAILED = "INGEST_FAILED"

    # Metadata errors
    METADATA_TIER1_FAILED = "METADATA_TIER1_FAILED"
    METADATA_TIER2_FAILED = "METADATA_TIER2_FAILED"

    # General errors
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


class TraceLevel(str, Enum):
    """Trace levels for debugging (HLD section 5: debug trace level)."""

    NONE = "none"
    BASIC = "basic"
    DETAILED = "detailed"
    FULL = "full"  # Captures intermediate prompts/responses


@dataclass
class DiagnosticEvent:
    """Structured diagnostic event."""

    timestamp: str
    level: str  # INFO, WARNING, ERROR
    component: str
    event_type: str
    message: str
    error_code: ErrorCode | None = None
    context: dict[str, Any] = field(default_factory=dict)
    trace_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PerformanceMetrics:
    """Performance metrics for operations."""

    operation: str
    duration_ms: float
    success: bool
    timestamp: str
    metadata: dict[str, Any] = field(default_factory=dict)


class DiagnosticsManager:
    """Centralized diagnostics manager for structured logging and metrics.

    Provides:
    - Structured error channels (HLD section 5)
    - Debug trace levels (HLD section 5)
    - Performance metrics tracking
    """

    def __init__(self, trace_level: TraceLevel = TraceLevel.BASIC):
        self.trace_level = trace_level
        self.logger = logging.getLogger("atlas.diagnostics")
        self.events: list[DiagnosticEvent] = []
        self.metrics: list[PerformanceMetrics] = []
        self._max_events: int = 5_000
        self._max_metrics: int = 10_000

    def log_event(
        self,
        *,
        level: str,
        component: str,
        event_type: str,
        message: str,
        error_code: ErrorCode | None = None,
        context: dict[str, Any] | None = None,
        trace_data: dict[str, Any] | None = None,
    ) -> None:
        """Log a structured diagnostic event."""
        event = DiagnosticEvent(
            timestamp=_utc_now_iso_z(),
            level=level,
            component=component,
            event_type=event_type,
            message=message,
            error_code=error_code,
            context=context or {},
            trace_data=trace_data or {},
        )
        self.events.append(event)
        if len(self.events) > self._max_events:
            self.events = self.events[-self._max_events:]

        # Log to standard logger
        log_method = getattr(self.logger, level.lower(), self.logger.info)
        log_msg = f"[{component}] {event_type}: {message}"
        if error_code:
            log_msg += f" (code: {error_code})"
        log_method(log_msg)

        # Include trace data if appropriate level
        if trace_data and self.trace_level in (TraceLevel.DETAILED, TraceLevel.FULL):
            self.logger.debug(f"Trace data: {trace_data}")

    def log_error(
        self,
        *,
        component: str,
        error_code: ErrorCode,
        message: str,
        context: dict[str, Any] | None = None,
        exception: Exception | None = None,
    ) -> None:
        """Log a structured error."""
        ctx = context or {}
        if exception:
            ctx["exception_type"] = type(exception).__name__
            ctx["exception_message"] = str(exception)

        self.log_event(
            level="ERROR",
            component=component,
            event_type="error",
            message=message,
            error_code=error_code,
            context=ctx,
        )

    def log_warning(
        self,
        *,
        component: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Log a warning event."""
        self.log_event(
            level="WARNING",
            component=component,
            event_type="warning",
            message=message,
            context=context,
        )

    def log_info(
        self,
        *,
        component: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Log an info event."""
        self.log_event(
            level="INFO",
            component=component,
            event_type="info",
            message=message,
            context=context,
        )

    def record_metric(
        self,
        *,
        operation: str,
        duration_ms: float,
        success: bool,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record a performance metric."""
        metric = PerformanceMetrics(
            operation=operation,
            duration_ms=duration_ms,
            success=success,
            timestamp=_utc_now_iso_z(),
            metadata=metadata or {},
        )
        self.metrics.append(metric)
        if len(self.metrics) > self._max_metrics:
            self.metrics = self.metrics[-self._max_metrics:]

        if self.trace_level in (TraceLevel.DETAILED, TraceLevel.FULL):
            self.logger.debug(f"Metric: {operation} took {duration_ms:.2f}ms (success={success})")

    @contextmanager
    def trace_operation(
        self,
        operation: str,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Context manager to trace an operation and record metrics.

        Usage:
            with diagnostics.trace_operation("embed_chunks", {"count": 10}) as ctx:
                ctx["chunks_processed"] = process_chunks()
        """
        start_time = time.time()
        context: dict[str, Any] = {}
        success = False

        try:
            yield context
            success = True
        except Exception:
            success = False
            raise
        finally:
            duration_ms = (time.time() - start_time) * 1000
            final_metadata = {**(metadata or {}), **context}
            self.record_metric(
                operation=operation,
                duration_ms=duration_ms,
                success=success,
                metadata=final_metadata,
            )

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of diagnostics and metrics."""
        errors = [e for e in self.events if e.level == "ERROR"]
        warnings = [e for e in self.events if e.level == "WARNING"]

        return {
            "total_events": len(self.events),
            "errors": len(errors),
            "warnings": len(warnings),
            "metrics_count": len(self.metrics),
            "recent_errors": [
                {"code": e.error_code, "message": e.message, "component": e.component}
                for e in errors[-5:]
            ],
            "performance_summary": {
                "total_operations": len(self.metrics),
                "successful_operations": sum(1 for m in self.metrics if m.success),
                "avg_duration_ms": (
                    sum(m.duration_ms for m in self.metrics) / len(self.metrics)
                    if self.metrics
                    else 0
                ),
            },
        }


# Global diagnostics instance
_global_diagnostics: DiagnosticsManager | None = None


def get_diagnostics(trace_level: TraceLevel = TraceLevel.BASIC) -> DiagnosticsManager:
    """Get or create the global diagnostics manager.

    If an instance already exists and a non-default trace_level is requested,
    update the existing instance's trace_level.
    """
    global _global_diagnostics
    if _global_diagnostics is None:
        _global_diagnostics = DiagnosticsManager(trace_level=trace_level)
    elif trace_level != TraceLevel.BASIC:
        # Allow callers to elevate or adjust trace level after initial creation
        _global_diagnostics.trace_level = trace_level
    return _global_diagnostics
