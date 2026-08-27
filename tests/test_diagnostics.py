"""Tests for diagnostics and error handling."""

import pytest

from atlas.diagnostics import (
    DiagnosticsManager,
    ErrorCode,
    TraceLevel,
    get_diagnostics,
)


def test_diagnostics_manager_creation():
    """Test creating diagnostics manager."""
    diag = DiagnosticsManager(trace_level=TraceLevel.BASIC)
    assert diag.trace_level == TraceLevel.BASIC
    assert len(diag.events) == 0
    assert len(diag.metrics) == 0


def test_log_error():
    """Test logging an error event."""
    diag = DiagnosticsManager()

    diag.log_error(
        component="test",
        error_code=ErrorCode.DOC_PARSE_FAILED,
        message="Test error",
        context={"detail": "test detail"},
    )

    assert len(diag.events) == 1
    event = diag.events[0]
    assert event.level == "ERROR"
    assert event.error_code == ErrorCode.DOC_PARSE_FAILED
    assert event.component == "test"
    assert event.context["detail"] == "test detail"


def test_log_info():
    """Test logging an info event."""
    diag = DiagnosticsManager()

    diag.log_info(
        component="test",
        message="Test info",
    )

    assert len(diag.events) == 1
    assert diag.events[0].level == "INFO"


def test_record_metric():
    """Test recording a performance metric."""
    diag = DiagnosticsManager()

    diag.record_metric(
        operation="test_operation",
        duration_ms=123.45,
        success=True,
        metadata={"count": 10},
    )

    assert len(diag.metrics) == 1
    metric = diag.metrics[0]
    assert metric.operation == "test_operation"
    assert metric.duration_ms == 123.45
    assert metric.success is True
    assert metric.metadata["count"] == 10


def test_trace_operation_success():
    """Test trace operation context manager on success."""
    diag = DiagnosticsManager()

    with diag.trace_operation("test_op") as ctx:
        ctx["result"] = "success"

    assert len(diag.metrics) == 1
    metric = diag.metrics[0]
    assert metric.operation == "test_op"
    assert metric.success is True
    assert metric.metadata["result"] == "success"


def test_trace_operation_failure():
    """Test trace operation context manager on failure."""
    diag = DiagnosticsManager()

    with pytest.raises(ValueError):
        with diag.trace_operation("test_op"):
            raise ValueError("Test error")

    assert len(diag.metrics) == 1
    metric = diag.metrics[0]
    assert metric.operation == "test_op"
    assert metric.success is False


def test_error_code_values():
    """Test error code enum values."""
    assert ErrorCode.DOC_PARSE_FAILED == "DOC_PARSE_FAILED"
    assert ErrorCode.JUDGE_INVALID_SCORE == "JUDGE_INVALID_SCORE"


def test_trace_level_values():
    """Test trace level enum values."""
    assert TraceLevel.NONE == "none"
    assert TraceLevel.BASIC == "basic"
    assert TraceLevel.DETAILED == "detailed"
    assert TraceLevel.FULL == "full"


def test_global_diagnostics():
    """Test global diagnostics singleton."""
    diag1 = get_diagnostics()
    diag2 = get_diagnostics()

    # Should return the same instance
    assert diag1 is diag2
