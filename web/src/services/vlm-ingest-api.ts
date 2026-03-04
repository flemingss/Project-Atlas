/**
 * Typed API client for Atlas VLM Ingest endpoints (`/api/editor/vlm-ingest/*`).
 *
 * Mirrors the Pydantic models in `src/atlas/api_vlm_ingest.py`.
 */

// ── Types ─────────────────────────────────────────────────────────

export interface StartSessionRequest {
  run_id: number;
  config?: Record<string, unknown> | null;
  headless?: boolean;
}

export interface StartSessionResponse {
  session_id: string;
  page_count: number;
  source_filename: string;
  status: string;
  headless: boolean;
}

export interface PageSettingsUpdate {
  page_num: number;
  enabled?: boolean | null;
  dpi?: number | null;
  crop_top?: number | null;
  crop_bottom?: number | null;
  crop_left?: number | null;
  crop_right?: number | null;
}

export interface UpdateConfigRequest {
  dpi?: number | null;
  crop_top?: number | null;
  crop_bottom?: number | null;
  crop_left?: number | null;
  crop_right?: number | null;
  system_prompt?: string | null;
  page_overrides?: PageSettingsUpdate[] | null;
}

export interface ProcessPageRequest {
  page_num?: number | null;
}

export interface ProcessPageResponse {
  page_num: number;
  markdown: string;
  model: string;
  status: string;
  finish_reason?: string | null;
}

export interface ProcessAllResponse {
  pages_processed: number;
  pages_skipped: number;
  pages_failed: number;
  errors: Record<number, string>;
  stitch: StitchResponse | null;
}

export interface StitchResponse {
  markdown: string;
  page_count: number;
  pages_processed: number;
  duplicate_lines_removed: number;
  tables_merged: number;
  headings_merged: number;
}

export interface CommitRequest {
  markdown?: string | null;
  feed_pipeline?: boolean;
  tenant_id?: string;
  project_id?: string;
  corpus_id?: string;
}

export interface CommitResponse {
  run_id: number | null;
  path: string;
  chars: number;
  chunks_upserted: number;
  message: string;
}

export interface SessionSummary {
  session_id: string;
  status: string;
  source_filename: string;
  run_id: number | null;
  page_count: number;
  headless: boolean;
  progress: Record<string, number>;
  config: Record<string, unknown>;
  pages?: Array<{
    page_num: number;
    status: string;
    enabled: boolean;
    markdown: string;
    model: string;
  }>;
}

export interface ExportConfigResponse {
  config: Record<string, unknown>;
  source_filename: string;
  page_count: number;
}

export interface ThumbnailEntry {
  page_num: number;
  thumbnail: string | null;
  enabled: boolean;
  status: string;
  error?: string;
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

function getAdminToken(): string | null {
  const stored = localStorage.getItem('atlas_admin_token');
  if (stored && stored.trim()) return stored;

  const token = (new URLSearchParams(window.location.search).get('token') || '').trim();
  if (token) {
    localStorage.setItem('atlas_admin_token', token);
    return token;
  }
  return null;
}

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

  /** List all active sessions. */
  listSessions() {
    return apiFetch<SessionSummary[]>(`${BASE}/sessions`, {
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
} as const;
