"""Human-in-the-Loop (HITL) integration for Project Atlas (HLD section 5).

Manages HITL workflow with Dify integration and priority queue.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Any

from atlas.diagnostics import get_diagnostics
from atlas.schemas import HITLTask


class HITLManager:
    """HITL Manager with priority queue and Dify integration (HLD section 5).

    HITL Hub (Pluggable): Dify-based interface with priority queue
    Priority: High-sensitivity + Low Score = Top
    Schema: before_md, after_md, reason_for_edit
    """

    def __init__(self):
        self.diagnostics = get_diagnostics()
        self.task_queue: list[HITLTask] = []
        self.completed_tasks: dict[str, HITLTask] = {}

    def create_task(
        self,
        *,
        doc_id: str,
        chunk_id: str,
        tenant_id: str,
        project_id: str,
        before_md: str,
        is_sensitive: bool,
        judge_score: float,
    ) -> HITLTask:
        """Create a new HITL task for review.

        Priority calculation (HLD: High-sensitivity + Low Score = Top):
        - Base priority: inverse of judge score (lower score = higher priority)
        - Sensitivity multiplier: 2x if sensitive
        """
        # Calculate priority score
        base_priority = 10.0 - judge_score  # Inverse of score (lower score = higher value)
        sensitivity_multiplier = 2.0 if is_sensitive else 1.0
        priority_score = base_priority * sensitivity_multiplier

        task = HITLTask(
            task_id=str(uuid.uuid4()),
            doc_id=doc_id,
            chunk_id=chunk_id,
            tenant_id=tenant_id,
            project_id=project_id,
            priority_score=priority_score,
            is_sensitive=is_sensitive,
            judge_score=judge_score,
            before_md=before_md,
            created_at=datetime.utcnow().isoformat() + "Z",
        )

        self.task_queue.append(task)
        self._sort_queue()

        self.diagnostics.log_info(
            component="hitl",
            message=f"Created HITL task with priority {priority_score:.2f}",
            context={
                "task_id": task.task_id,
                "doc_id": doc_id,
                "judge_score": judge_score,
                "is_sensitive": is_sensitive,
            },
        )

        return task

    def _sort_queue(self) -> None:
        """Sort queue by priority (highest first)."""
        self.task_queue.sort(key=lambda t: t.priority_score, reverse=True)

    def get_next_task(self, *, assigned_to: str | None = None) -> HITLTask | None:
        """Get the next highest priority task.

        Args:
            assigned_to: User/agent to assign the task to

        Returns:
            Next task or None if queue is empty
        """
        pending_tasks = [t for t in self.task_queue if t.status == "pending"]

        if not pending_tasks:
            return None

        task = pending_tasks[0]
        task.status = "in_progress"
        if assigned_to:
            task.assigned_to = assigned_to

        self.diagnostics.log_info(
            component="hitl",
            message=f"Assigned task {task.task_id} to {assigned_to}",
        )

        return task

    def complete_task(
        self, *, task_id: str, after_md: str, reason_for_edit: str
    ) -> bool:
        """Mark a task as completed with edits.

        Args:
            task_id: ID of the task
            after_md: Edited markdown
            reason_for_edit: Explanation of changes

        Returns:
            True if task was found and completed
        """
        for task in self.task_queue:
            if task.task_id == task_id:
                task.status = "completed"
                task.after_md = after_md
                task.reason_for_edit = reason_for_edit
                task.completed_at = datetime.utcnow().isoformat() + "Z"

                self.completed_tasks[task_id] = task

                self.diagnostics.log_info(
                    component="hitl",
                    message=f"Completed HITL task {task_id}",
                    context={"reason": reason_for_edit},
                )

                return True

        return False

    def skip_task(self, *, task_id: str) -> bool:
        """Skip a task without edits.

        Returns:
            True if task was found and skipped
        """
        for task in self.task_queue:
            if task.task_id == task_id:
                task.status = "skipped"
                task.completed_at = datetime.utcnow().isoformat() + "Z"

                self.diagnostics.log_info(
                    component="hitl",
                    message=f"Skipped HITL task {task_id}",
                )

                return True

        return False

    def get_queue_status(self) -> dict[str, Any]:
        """Get current queue status."""
        pending = [t for t in self.task_queue if t.status == "pending"]
        in_progress = [t for t in self.task_queue if t.status == "in_progress"]
        completed = [t for t in self.task_queue if t.status == "completed"]
        skipped = [t for t in self.task_queue if t.status == "skipped"]

        return {
            "total_tasks": len(self.task_queue),
            "pending": len(pending),
            "in_progress": len(in_progress),
            "completed": len(completed),
            "skipped": len(skipped),
            "top_priority_task": asdict(pending[0]) if pending else None,
        }

    def get_task_by_id(self, task_id: str) -> HITLTask | None:
        """Get a task by its ID."""
        for task in self.task_queue:
            if task.task_id == task_id:
                return task
        return self.completed_tasks.get(task_id)

    async def push_to_dify(self, task: HITLTask) -> bool:
        """Push a task to Dify for human review.

        NOTE: This is a placeholder. Full implementation would:
        - Connect to Dify API
        - Create a review workflow
        - Handle callbacks for completed reviews
        """
        self.diagnostics.log_warning(
            component="hitl",
            message="Dify integration not yet implemented - placeholder",
            context={"task_id": task.task_id},
        )
        return False


# Global HITL manager instance
_global_hitl_manager: HITLManager | None = None


def get_hitl_manager() -> HITLManager:
    """Get or create the global HITL manager."""
    global _global_hitl_manager
    if _global_hitl_manager is None:
        _global_hitl_manager = HITLManager()
    return _global_hitl_manager
