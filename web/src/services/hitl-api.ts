/**
 * HITL API service — covers /admin/hitl/* endpoints.
 * Human-in-the-loop review task management.
 */
import { apiFetch } from './shared';

// ── Types ─────────────────────────────────────────────────────────

export interface HitlTask {
  task_id: number;
  doc_id: string;
  doc_version: string;
  chunk_id?: string;
  chunk_index?: number;
  status: 'pending' | 'completed' | 'skipped' | 'rejected';
  before_md?: string;
  after_md?: string;
  reason?: string;
  created_at?: string;
  updated_at?: string;
  meta?: {
    source?: string;
    judge_score?: number;
    judge_sub_scores?: Record<string, number>;
    judge_rationale?: string;
    score_history?: number[];
    refine_attempts?: number;
    max_refine_attempts?: number;
    last_improvements?: string[];
    is_sensitive?: boolean;
    [key: string]: unknown;
  };
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
  completeTask(taskId: number, payload: { after_md: string; reason?: string }) {
    return apiFetch<{ status: string }>(`/admin/hitl/tasks/${taskId}/complete`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  /** Skip a task with a reason. */
  skipTask(taskId: number, payload: { reason: string }) {
    return apiFetch<{ status: string }>(`/admin/hitl/tasks/${taskId}/skip`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  /** Reject a task. */
  rejectTask(taskId: number, payload?: { reason?: string }) {
    return apiFetch<{ status: string }>(`/admin/hitl/tasks/${taskId}/reject`, {
      method: 'POST',
      body: JSON.stringify(payload ?? {}),
    });
  },

  /** Resume the pipeline for a completed task. */
  resumeTask(taskId: number) {
    return apiFetch<{ status: string; message?: string }>(`/admin/hitl/tasks/${taskId}/resume`, {
      method: 'POST',
    });
  },
};
