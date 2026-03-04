/**
 * RAG API service — covers /rag/* endpoints.
 * File/text ingest and semantic search.
 */
import { apiFetch, authHeaders } from './shared';

// ── Types ─────────────────────────────────────────────────────────

export interface IngestTextRequest {
  text: string;
  doc_name?: string;
  doc_id?: string;
  doc_version?: string;
  is_finalized?: boolean;
  is_sensitive?: boolean;
  tenant_id?: string;
  project_id?: string;
  corpus_id?: string;
}

export interface IngestResponse {
  ok: boolean;
  collection: string;
  doc_id: string;
  chunks_upserted: number;
  error_code?: string | null;
  error_message?: string | null;
}

export interface SearchRequest {
  query: string;
  top_k?: number;
  fidelity_mode?: 'verified' | 'verified+partial' | 'all';
  tenant_id?: string;
  project_id?: string;
  corpus_id?: string;
}

export interface SearchHit {
  score: number;
  text: string;
  doc_id: string;
  doc_version?: string;
  chunk_index?: number;
  is_finalized?: boolean;
  source?: string;
  payload?: Record<string, unknown>;
}

export interface SearchResponse {
  ok: boolean;
  collection: string;
  hits: SearchHit[];
}

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
