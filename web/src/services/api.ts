/**
 * Typed API client for Atlas `/api/editor/*` endpoints.
 *
 * Every function returns the parsed JSON body directly and throws
 * on non-2xx responses with a useful error message.
 */

// ── Types (generated from the backend OpenAPI schema) ─────────────

import type {
  DocResolveResponse,
  LlmRefineRequest,
  LlmRefineResponse,
  PageInfoResponse,
  ReJudgeRequest,
  ReJudgeResponse,
  SaveMarkdownRequest,
  SaveMarkdownResponse,
  VisionRefineRequest,
  VisionRefineResponse,
} from './api-contracts';

export type {
  DocResolveResponse,
  PageInfoResponse,
  VisionRefineRequest,
  VisionRefineResponse,
  SaveMarkdownRequest,
  SaveMarkdownResponse,
  LlmRefineRequest,
  LlmRefineResponse,
  ReJudgeRequest,
  ReJudgeResponse,
};

/** Backend returns an untyped dict for page-markdown — hand-typed. */
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
