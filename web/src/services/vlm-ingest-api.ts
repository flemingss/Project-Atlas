/**
 * Typed API client for Atlas VLM Ingest endpoints (`/api/editor/vlm-ingest/*`).
 *
 * Mirrors the Pydantic models in `src/atlas/api_vlm_ingest.py`.
 */

// ── Types (generated from the backend OpenAPI schema) ─────────────

import type {
  CommitRequest,
  CommitResponse,
  ExportConfigResponse,
  PageSettingsUpdate,
  PageSummary,
  ProcessAllResponse,
  ProcessPageRequest,
  ProcessPageResponse,
  ResumableSession,
  SessionSummary,
  StartSessionRequest,
  StartSessionResponse,
  StitchResponse,
  UpdateConfigRequest,
} from './api-contracts';
// Admin-token handling lives in exactly one place — see services/shared.ts.
import { getAdminToken } from './shared';

export type {
  StartSessionRequest,
  StartSessionResponse,
  PageSettingsUpdate,
  PageSummary,
  UpdateConfigRequest,
  ProcessPageRequest,
  ProcessPageResponse,
  ProcessAllResponse,
  StitchResponse,
  CommitRequest,
  CommitResponse,
  SessionSummary,
  ResumableSession,
  ExportConfigResponse,
};

export interface ThumbnailEntry {
  page_num: number;
  thumbnail: string | null;
  enabled: boolean;
  status: string;
  error?: string;
}

export interface PageAnalysisResult {
  content_class: string; // 'text-native' | 'image-heavy' | 'image-only'
  text_chars: number;
  image_ratio: number;
  image_rects: Array<{ x: number; y: number; w: number; h: number }>;
  error?: string;
}

export interface PageAnalysisResponse {
  pages: Record<number, PageAnalysisResult>;
}

export interface PageResultResponse {
  page_num: number;
  status: string;
  markdown?: string;
  model?: string;
  dpi?: number;
  error?: string;
  settings: {
    enabled: boolean;
    dpi: number;
    crop_top: number;
    crop_bottom: number;
    crop_left: number;
    crop_right: number;
  };
}

export interface PreviewImageOptions {
  dpi?: number;
  cropTop?: number;
  cropBottom?: number;
  cropLeft?: number;
  cropRight?: number;
  applyCrop?: boolean;
}

// ── Helpers ───────────────────────────────────────────────────────

function jsonHeaders(): HeadersInit {
  const h: Record<string, string> = { 'Content-Type': 'application/json' };
  const token = getAdminToken();
  if (token) h['X-Atlas-Admin-Token'] = token;
  return h;
}

function authHeaders(): HeadersInit {
  const h: Record<string, string> = {};
  const token = getAdminToken();
  if (token) h['X-Atlas-Admin-Token'] = token;
  return h;
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, { ...init });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`API ${resp.status}: ${text}`);
  }
  return resp.json() as Promise<T>;
}

const BASE = '/api/editor/vlm-ingest';

// ── Public API ────────────────────────────────────────────────────

export const vlmIngestApi = {
  /** Start a session from an existing pipeline run. */
  startSession(req: StartSessionRequest) {
    return apiFetch<StartSessionResponse>(`${BASE}/start`, {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify(req),
    });
  },

  /** Start a session by uploading a PDF file. */
  startSessionUpload(file: File, config?: Record<string, unknown>, headless?: boolean) {
    const form = new FormData();
    form.append('file', file);
    if (config) form.append('config', JSON.stringify(config));
    if (headless) form.append('headless', 'true');
    return apiFetch<StartSessionResponse>(`${BASE}/start-upload`, {
      method: 'POST',
      headers: authHeaders(),
      body: form,
    });
  },

  /** List sessions that can be resumed, newest first.
   *
   *  Backed by the ledger rather than the server's in-memory cache, so a
   *  session the server has released from RAM still appears here — it is
   *  rehydrated on demand when opened.
   */
  listSessions() {
    return apiFetch<ResumableSession[]>(`${BASE}/sessions`, {
      headers: jsonHeaders(),
    });
  },

  /** Get session details. */
  getSession(sid: string) {
    return apiFetch<SessionSummary>(`${BASE}/${sid}`, {
      headers: jsonHeaders(),
    });
  },

  /** Delete a session. */
  deleteSession(sid: string) {
    return apiFetch<{ message: string }>(`${BASE}/${sid}`, {
      method: 'DELETE',
      headers: jsonHeaders(),
    });
  },

  /** Update global and per-page config. */
  updateConfig(sid: string, req: UpdateConfigRequest) {
    return apiFetch<SessionSummary>(`${BASE}/${sid}/config`, {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify(req),
    });
  },

  /** Export config for headless reuse. */
  exportConfig(sid: string) {
    return apiFetch<ExportConfigResponse>(`${BASE}/${sid}/export-config`, {
      headers: jsonHeaders(),
    });
  },

  /** Get page thumbnails. */
  getThumbnails(sid: string, dpi = 72) {
    return apiFetch<ThumbnailEntry[]>(`${BASE}/${sid}/thumbnails?dpi=${dpi}`, {
      headers: jsonHeaders(),
    });
  },

  /** Get preview image bytes for a page, with optional temporary rendering overrides. */
  async previewImage(
    sid: string,
    pageNum: number,
    opts: PreviewImageOptions = {},
  ): Promise<Blob> {
    const q = new URLSearchParams();
    if (opts.dpi != null) q.set('dpi', String(opts.dpi));
    if (opts.cropTop != null) q.set('crop_top', String(opts.cropTop));
    if (opts.cropBottom != null) q.set('crop_bottom', String(opts.cropBottom));
    if (opts.cropLeft != null) q.set('crop_left', String(opts.cropLeft));
    if (opts.cropRight != null) q.set('crop_right', String(opts.cropRight));
    if (opts.applyCrop != null) q.set('apply_crop', opts.applyCrop ? 'true' : 'false');

    const suffix = q.toString() ? `?${q.toString()}` : '';
    const resp = await fetch(`${BASE}/${sid}/preview/${pageNum}${suffix}`, {
      headers: authHeaders(),
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`API ${resp.status}: ${text}`);
    }
    return resp.blob();
  },

  /** Process a page through VLM. */
  processPage(sid: string, req: ProcessPageRequest) {
    return apiFetch<ProcessPageResponse>(`${BASE}/${sid}/process-page`, {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify(req),
    });
  },

  /** Process ALL pending pages sequentially on the server, then auto-stitch. */
  processAll(sid: string) {
    return apiFetch<ProcessAllResponse>(`${BASE}/${sid}/process-all`, {
      method: 'POST',
      headers: jsonHeaders(),
    });
  },

  /** Stitch all processed pages. */
  stitch(sid: string) {
    return apiFetch<StitchResponse>(`${BASE}/${sid}/stitch`, {
      method: 'POST',
      headers: jsonHeaders(),
    });
  },

  /** Commit stitched result as artifact. */
  commit(sid: string, req: CommitRequest) {
    return apiFetch<CommitResponse>(`${BASE}/${sid}/commit`, {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify(req),
    });
  },

  /** Update page result (operator correction). */
  updatePageResult(sid: string, pageNum: number, markdown: string) {
    return apiFetch<{ page_num: number; status: string }>(
      `${BASE}/${sid}/page-result/${pageNum}`,
      {
        method: 'PUT',
        headers: jsonHeaders(),
        body: JSON.stringify({ markdown }),
      },
    );
  },

  /** Get page content analysis (text-native / image-heavy / image-only). */
  getPageAnalysis(sid: string) {
    return apiFetch<PageAnalysisResponse>(`${BASE}/${sid}/page-analysis`, {
      headers: jsonHeaders(),
    });
  },
} as const;
