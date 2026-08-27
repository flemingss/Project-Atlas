/**
 * HITL API service — covers /admin/hitl/* endpoints.
 * Human-in-the-loop review task management.
 */
import { apiFetch } from './shared';
import type { HitlTaskResponse } from './api-contracts';

// ── Types (generated from the backend OpenAPI schema) ─────────────

export type HitlTask = HitlTaskResponse;

// ── Service ───────────────────────────────────────────────────────

export const hitlApi = {
  /** List HITL tasks, optionally filtered by status. */
  listTasks(params?: { status?: string; tenant_id?: string; limit?: number }) {
    const q = new URLSearchParams();
    if (params?.status) q.set('status', params.status);
    if (params?.tenant_id) q.set('tenant_id', params.tenant_id);
    if (params?.limit) q.set('limit', String(params.limit));
    const qs = q.toString();
    return apiFetch<HitlTask[]>(`/admin/hitl/tasks${qs ? `?${qs}` : ''}`);
  },

  /** Get a specific task by ID. */
  getTask(taskId: number) {
    return apiFetch<HitlTask>(`/admin/hitl/tasks/${taskId}`);
  },

  /** Claim the next pending task (highest priority first, any workspace —
   *  the backend's only filter is assigned_to, which the UI doesn't use). */
  nextTask(params?: { assigned_to?: string }) {
    const q = params?.assigned_to ? `?assigned_to=${encodeURIComponent(params.assigned_to)}` : '';
    return apiFetch<HitlTask>(`/admin/hitl/tasks/next${q}`, { method: 'POST' });
  },

  /** Complete a task with edited markdown and reason. */
  completeTask(taskId: number, payload: { after_md: string; reason_for_edit?: string }) {
    return apiFetch<HitlTask>(`/admin/hitl/tasks/${taskId}/complete`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  /** Skip a task with a reason. */
  skipTask(taskId: number, payload: { reason: string }) {
    return apiFetch<HitlTask>(`/admin/hitl/tasks/${taskId}/skip`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  /** Reject a task. */
  rejectTask(taskId: number, payload?: { reason?: string }) {
    return apiFetch<HitlTask>(`/admin/hitl/tasks/${taskId}/reject`, {
      method: 'POST',
      body: JSON.stringify(payload ?? {}),
    });
  },

  /** Resume the pipeline for a completed task. */
  resumeTask(taskId: number) {
    return apiFetch<Record<string, unknown>>(`/admin/hitl/tasks/${taskId}/resume`, {
      method: 'POST',
    });
  },
};
