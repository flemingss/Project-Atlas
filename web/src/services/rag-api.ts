/**
 * RAG API service — covers /rag/* endpoints.
 * File/text ingest and semantic search.
 */
import { apiFetch, authHeaders } from './shared';
import type {
  IngestTextRequest,
  IngestTextResponse,
  SearchHit,
  SearchRequest,
  SearchResponse,
} from './api-contracts';

// ── Types (generated from the backend OpenAPI schema) ─────────────

export type { IngestTextRequest, SearchRequest, SearchHit, SearchResponse };
/** Kept under its historical export name; identical to IngestTextResponse. */
export type IngestResponse = IngestTextResponse;

// ── Service ───────────────────────────────────────────────────────

export const ragApi = {
  /** Ingest raw text as a document. */
  ingestText(payload: IngestTextRequest) {
    return apiFetch<IngestResponse>('/rag/ingest/text', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  /** Ingest a file upload. */
  ingestFile(formData: FormData) {
    return fetch('/rag/ingest/file', {
      method: 'POST',
      headers: authHeaders(),
      body: formData,
    }).then(async (resp) => {
      if (!resp.ok) {
        const text = await resp.text();
        throw new Error(`API ${resp.status}: ${text}`);
      }
      return resp.json() as Promise<IngestResponse>;
    });
  },

  /** Semantic search across the knowledge base. */
  search(payload: SearchRequest) {
    return apiFetch<SearchResponse>('/rag/search', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },
};
