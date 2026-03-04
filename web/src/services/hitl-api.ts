/**
 * HITL API service — covers /admin/hitl/* endpoints.
 * Human-in-the-loop review task management.
 */
import { apiFetch } from './shared';

// ── Types ─────────────────────────────────────────────────────────

export interface HitlTask {
  id: number;
  run_id: number;
  tenant_id: string;
  project_id: string;
  doc_id: string;
  doc_version: string;
  chunk_id: string;
  priority_score: number;
  is_sensitive: boolean;
  judge_score: number;
  status: string;
  assigned_to: string;
  before_md: string;
  after_md: string;
  reason_for_edit: string;
  meta: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface HitlTaskListResponse {
  tasks: HitlTask[];
  total: number;
}

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

  /** Claim the next pending task. */
  nextTask(params?: { tenant_id?: string }) {
    const q = params?.tenant_id ? `?tenant_id=${encodeURIComponent(params.tenant_id)}` : '';
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
