/**
 * Typed API client for Atlas `/api/editor/*` endpoints.
 *
 * Every function returns the parsed JSON body directly and throws
 * on non-2xx responses with a useful error message.
 */

// ── Types matching the backend Pydantic models ────────────────────

export interface DocResolveResponse {
  doc_id: string;
  run_id: number;
  doc_version: string;
  status: string;
  source_filename: string;
}

export interface PageInfoResponse {
  run_id: number;
  page_count: number;
  source_filename: string;
  source_mime_type: string;
}

export interface VisionRefineRequest {
  run_id: number;
  page_num: number;
  current_markdown: string;
  dpi?: number;
  crop_top?: number;
  crop_bottom?: number;
  system_prompt?: string | null;
}

export interface VisionRefineResponse {
  corrected_markdown: string;
  model: string;
  page_num: number;
  finish_reason?: string | null;
}

export interface SaveMarkdownRequest {
  run_id: number;
  markdown: string;
}

export interface SaveMarkdownResponse {
  run_id: number;
  path: string;
  chars: number;
  message: string;
}

export interface LlmRefineRequest {
  run_id: number;
  markdown: string;
}

export interface LlmRefineResponse {
  refined_markdown: string;
  model: string;
  success: boolean;
  improvements: string[];
}

export interface ReJudgeRequest {
  run_id: number;
  markdown: string;
  judge_cutoff?: number;
}

export interface ReJudgeResponse {
  score: number;
  sub_scores: Record<string, number>;
  rationale: string;
  needs_refinement: boolean;
  model: string;
}

export interface PageMarkdownResponse {
  run_id: number;
  page_num: number;
  markdown: string;
}

// ── Helpers (re-exported from shared) ─────────────────────────────

import { apiFetch, getAdminToken } from './shared';
export { apiFetch, getAdminToken };

// ── Public API ────────────────────────────────────────────────────

export const editorApi = {
  resolveDoc(docId: string) {
    return apiFetch<DocResolveResponse>(
      `/api/editor/resolve-doc/${encodeURIComponent(docId)}`,
    );
  },

  pageInfo(runId: number) {
    return apiFetch<PageInfoResponse>(`/api/editor/page-info/${runId}`);
  },

  sourcePdfUrl(runId: number): string {
    return `/api/editor/source-pdf/${runId}`;
  },

  markdown(runId: number) {
    return apiFetch<{ run_id: number; markdown: string }>(
      `/api/editor/markdown/${runId}`,
    );
  },

  pageMarkdown(runId: number, pageNum: number) {
    return apiFetch<PageMarkdownResponse>(
      `/api/editor/page-markdown/${runId}/${pageNum}`,
    );
  },

  visionRefine(req: VisionRefineRequest) {
    return apiFetch<VisionRefineResponse>('/api/editor/vision-refine', {
      method: 'POST',
      body: JSON.stringify(req),
    });
  },

  saveMarkdown(req: SaveMarkdownRequest) {
    return apiFetch<SaveMarkdownResponse>('/api/editor/save-markdown', {
      method: 'POST',
      body: JSON.stringify(req),
    });
  },

  llmRefine(req: LlmRefineRequest) {
    return apiFetch<LlmRefineResponse>('/api/editor/llm-refine', {
      method: 'POST',
      body: JSON.stringify(req),
    });
  },

  reJudge(req: ReJudgeRequest) {
    return apiFetch<ReJudgeResponse>('/api/editor/re-judge', {
      method: 'POST',
      body: JSON.stringify(req),
    });
  },
} as const;
