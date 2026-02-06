"""Tests for HITL (Human-in-the-Loop) management."""


from atlas.hitl import HITLManager, get_hitl_manager


def test_hitl_manager_creation():
    """Test creating HITL manager."""
    manager = HITLManager()
    assert len(manager.task_queue) == 0
    assert len(manager.completed_tasks) == 0


def test_create_task():
    """Test creating a HITL task."""
    manager = HITLManager()

    task = manager.create_task(
        doc_id="doc-123",
        chunk_id="chunk-456",
        tenant_id="test-tenant",
        project_id="test-project",
        before_md="Original markdown",
        is_sensitive=True,
        judge_score=2.5,
    )

    assert task.doc_id == "doc-123"
    assert task.chunk_id == "chunk-456"
    assert task.is_sensitive is True
    assert task.judge_score == 2.5
    assert task.status == "pending"
    assert len(manager.task_queue) == 1


def test_task_priority_calculation():
    """Test priority calculation (HLD: High-sensitivity + Low Score = Top)."""
    manager = HITLManager()

    # Low score, sensitive - should have high priority
    task1 = manager.create_task(
        doc_id="doc-1",
        chunk_id="chunk-1",
        tenant_id="test-tenant",
        project_id="test-project",
        before_md="Content 1",
        is_sensitive=True,
        judge_score=2.0,  # Low score
    )

    # High score, not sensitive - should have low priority
    _ = manager.create_task(
        doc_id="doc-2",
        chunk_id="chunk-2",
        tenant_id="test-tenant",
        project_id="test-project",
        before_md="Content 2",
        is_sensitive=False,
        judge_score=4.5,  # High score
    )

    # task1 should have higher priority than the second task
    assert task1.priority_score > manager.task_queue[1].priority_score


def test_queue_sorting():
    """Test that queue is sorted by priority."""
    manager = HITLManager()

    # Create tasks in reverse priority order
    manager.create_task(
        doc_id="doc-1",
        chunk_id="chunk-1",
        tenant_id="test-tenant",
        project_id="test-project",
        before_md="Content",
        is_sensitive=False,
        judge_score=4.0,
    )

    manager.create_task(
        doc_id="doc-2",
        chunk_id="chunk-2",
        tenant_id="test-tenant",
        project_id="test-project",
        before_md="Content",
        is_sensitive=True,
        judge_score=2.0,
    )

    # First task in queue should be the highest priority
    assert manager.task_queue[0].doc_id == "doc-2"
    assert manager.task_queue[1].doc_id == "doc-1"


def test_get_next_task():
    """Test getting next task from queue."""
    manager = HITLManager()

    task = manager.create_task(
        doc_id="doc-1",
        chunk_id="chunk-1",
        tenant_id="test-tenant",
        project_id="test-project",
        before_md="Content",
        is_sensitive=True,
        judge_score=2.0,
    )

    next_task = manager.get_next_task(assigned_to="user@example.com")

    assert next_task is not None
    assert next_task.task_id == task.task_id
    assert next_task.status == "in_progress"
    assert next_task.assigned_to == "user@example.com"


def test_complete_task():
    """Test completing a HITL task."""
    manager = HITLManager()

    task = manager.create_task(
        doc_id="doc-1",
        chunk_id="chunk-1",
        tenant_id="test-tenant",
        project_id="test-project",
        before_md="Original",
        is_sensitive=True,
        judge_score=2.0,
    )

    success = manager.complete_task(
        task_id=task.task_id,
        after_md="Improved markdown",
        reason_for_edit="Fixed OCR errors",
    )

    assert success is True
    assert task.status == "completed"
    assert task.after_md == "Improved markdown"
    assert task.reason_for_edit == "Fixed OCR errors"
    assert task.task_id in manager.completed_tasks


def test_skip_task():
    """Test skipping a HITL task."""
    manager = HITLManager()

    task = manager.create_task(
        doc_id="doc-1",
        chunk_id="chunk-1",
        tenant_id="test-tenant",
        project_id="test-project",
        before_md="Content",
        is_sensitive=True,
        judge_score=2.0,
    )

    success = manager.skip_task(task_id=task.task_id)

    assert success is True
    assert task.status == "skipped"


def test_get_queue_status():
    """Test getting queue status."""
    manager = HITLManager()

    # Create tasks in various states
    task1 = manager.create_task(
        doc_id="doc-1",
        chunk_id="chunk-1",
        tenant_id="test-tenant",
        project_id="test-project",
        before_md="Content",
        is_sensitive=True,
        judge_score=2.0,
    )

    _ = manager.create_task(
        doc_id="doc-2",
        chunk_id="chunk-2",
        tenant_id="test-tenant",
        project_id="test-project",
        before_md="Content",
        is_sensitive=False,
        judge_score=3.0,
    )

    manager.get_next_task()  # Moves one to in_progress
    manager.complete_task(task_id=task1.task_id, after_md="Fixed", reason_for_edit="Improved")

    status = manager.get_queue_status()

    assert status["total_tasks"] == 2
    assert status["pending"] == 1
    assert status["in_progress"] == 0
    assert status["completed"] == 1
    assert status["skipped"] == 0


def test_global_hitl_manager():
    """Test global HITL manager singleton."""
    manager1 = get_hitl_manager()
    manager2 = get_hitl_manager()

    # Should return the same instance
    assert manager1 is manager2
