/**
 * Admin API service — covers /admin/* endpoints.
 * Groups, config, workflow ledger, doc operations, cleanup, export/import.
 */
import { apiFetch, apiFetchRaw } from './shared';

// ── Types ─────────────────────────────────────────────────────────

export interface Tenant {
  tenant_id: string;
  display_name?: string;
}
export interface Project {
  project_id: string;
  tenant_id: string;
  display_name?: string;
}
export interface Corpus {
  corpus_id: string;
  project_id: string;
  tenant_id: string;
  display_name?: string;
}

export interface RunSummary {
  id: number;
  status: string;
  tenant_id: string;
  project_id: string;
  doc_id: string;
  doc_version: string;
  current_node: string;
  error_code: string;
  error_message: string;
  meta: Record<string, unknown>;
  updated_at: string;
  created_at: string;
}
export interface RunDetail extends RunSummary {
  node_runs?: NodeRun[];
  artifacts?: Artifact[];
}
export interface NodeRun {
  id: number;
  run_id: number;
  node_name: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  input_ref: string;
  output_ref: string;
  error_code: string;
  error_message: string;
  created_at: string;
}
export interface Artifact {
  id: number;
  run_id: number;
  node_run_id: number | null;
  kind: string;
  path: string;
  sha256: string;
  mime_type: string;
  meta: Record<string, unknown>;
  created_at: string;
}

export interface ConfigVersionSummary {
  id: number;
  name: string;
  notes: string;
  is_active: boolean;
  config_hash: string;
  created_at: string;
}
export interface EffectiveConfig {
  [key: string]: unknown;
}

export interface CleanupFeedback {
  id: number;
  tenant_id: string;
  project_id: string;
  corpus_id: string;
  doc_id: string;
  chunk_id: string;
  run_id: number | null;
  category: string;
  description: string;
  source_span_start: number | null;
  source_span_end: number | null;
  created_by: string;
  meta: Record<string, unknown>;
  created_at: string;
}
export interface CleanupFeedbackSummary {
  categories: Record<string, number>;
  total: number;
}
export interface CleanupRuleSuggestion {
  yaml: string;
  rationale: string;
  warnings?: string[];
}

export interface DocInfo {
  doc_id: string;
  tenant_id?: string;
  project_id?: string;
  corpus_id?: string;
  doc_version?: string;
  source_mime_type?: string;
  is_finalized?: boolean;
  is_sensitive?: boolean;
  created_at?: string;
}

export interface LookingGlassMetrics {
  scope: { tenant_id: string | null; project_id: string | null; corpus_id: string | null };
  workflow_runs: {
    total: number;
    by_status: Record<string, number>;
    completion_rate: number;
    failure_rate: number;
  };
  node_runs: {
    total: number;
    failed: number;
    failure_rate: number;
    failures_by_node: Record<string, number>;
  };
  hitl: {
    total: number;
    by_status: Record<string, number>;
    escalation_rate: number;
  };
  auto_accepted: {
    count: number;
    rate: number;
  };
  cleanup_feedback: {
    total: number;
    by_category: Record<string, number>;
  };
  [key: string]: unknown;
}

export interface LookingGlassQdrant {
  [key: string]: unknown;
}

export interface DocActiveVersion {
  tenant_id?: string;
  project_id?: string;
  corpus_id?: string;
  doc_id: string;
  active_doc_version: string;
  latest_doc_version?: string;
}

export interface ChunkPreview {
  chunk_index: number;
  doc_version: string;
  is_finalized: boolean;
  text: string;
  [key: string]: unknown;
}

export interface OrphanGroup {
  tenant_id: string;
  project_id: string;
  doc_id: string;
  doc_version: string;
  points: number;
}

export interface DanglingRun {
  run_id: number;
  tenant_id: string;
  project_id: string;
  doc_id: string;
  doc_version: string;
  status: string;
  current_node: string;
  corpus_id: string;
  created_at: string | null;
}

export interface OrphanScanResult {
  ok: boolean;
  dry_run: boolean;
  scanned_points: number;
  max_points: number;
  orphan_groups: number;
  orphan_points_estimated: number;
  deleted_groups: number;
  sample_orphans: OrphanGroup[];
  dangling_runs: DanglingRun[];
}

export interface AdoptOrphanGroupRequest {
  old_tenant_id: string;
  old_project_id: string;
  old_doc_id: string;
  old_doc_version: string;
  tenant_id: string;
  project_id: string;
  corpus_id: string;
}

export interface AdoptOrphanGroupResult {
  ok: boolean;
  run_id: number;
  doc_id: string;
  doc_version: string;
  from: { tenant_id: string; project_id: string };
  to: { tenant_id: string; project_id: string; corpus_id: string };
  qdrant_payload_updated: boolean;
  qdrant_error: string;
}

// ── Service ───────────────────────────────────────────────────────

export const adminApi = {
  // ── DB ──
  resetDb(payload: { reset_postgres?: boolean; clear_qdrant?: boolean; clear_artifacts?: boolean }) {
    return apiFetch<{ status: string }>('/admin/db/reset', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  // ── Config ──
  effectiveConfig() {
    return apiFetch<EffectiveConfig>('/admin/config/effective');
  },
  restoreStockConfig(payload: { restore_pipeline?: boolean; restore_models?: boolean }) {
    return apiFetch<{ status: string }>('/admin/config/restore-stock', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },
  configVersions() {
    return apiFetch<ConfigVersionSummary[]>('/admin/config-versions');
  },
  createConfigVersion(payload: { config: Record<string, unknown>; comment?: string }) {
    return apiFetch<ConfigVersionSummary>('/admin/config-versions', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },
  activateConfigVersion(configId: number) {
    return apiFetch<{ status: string }>(`/admin/config-versions/${configId}/activate`, {
      method: 'POST',
    });
  },
  validateRules(payload: { rules: unknown }) {
    return apiFetch<{ valid: boolean; errors?: string[] }>('/admin/config/validate-rules', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },
  reloadYaml() {
    return apiFetch<{ status: string }>('/admin/reload-yaml', { method: 'POST' });
  },

  // ── Groups (tenants / projects / corpora) ──
  async listTenants() {
    const resp = await apiFetch<{ tenants: Tenant[] }>('/admin/tenants');
    return resp.tenants;
  },
  createTenant(payload: { tenant_id: string; display_name?: string }) {
    return apiFetch<Tenant>('/admin/tenants', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },
  deleteTenant(tenantId: string) {
    return apiFetch<{ status: string }>(`/admin/tenants/${encodeURIComponent(tenantId)}`, {
      method: 'DELETE',
    });
  },

  async listProjects(params?: { tenant_id?: string }) {
    const q = params?.tenant_id ? `?tenant_id=${encodeURIComponent(params.tenant_id)}` : '';
    const resp = await apiFetch<{ projects: Project[] }>(`/admin/projects${q}`);
    return resp.projects;
  },
  createProject(payload: { project_id: string; tenant_id: string; display_name?: string }) {
    return apiFetch<Project>('/admin/projects', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },
  deleteProject(projectId: string) {
    return apiFetch<{ status: string }>(`/admin/projects/${encodeURIComponent(projectId)}`, {
      method: 'DELETE',
    });
  },

  async listCorpora(params?: { project_id?: string }) {
    const q = params?.project_id ? `?project_id=${encodeURIComponent(params.project_id)}` : '';
    const resp = await apiFetch<{ corpora: Corpus[] }>(`/admin/corpora${q}`);
    return resp.corpora;
  },
  createCorpus(payload: { corpus_id: string; project_id: string; tenant_id: string; display_name?: string }) {
    return apiFetch<Corpus>('/admin/corpora', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },
  deleteCorpus(corpusId: string) {
    return apiFetch<{ status: string }>(`/admin/corpora/${encodeURIComponent(corpusId)}`, {
      method: 'DELETE',
    });
  },

  // ── Workflow ledger (runs) ──
  listRuns(params?: { limit?: number }) {
    const q = params?.limit ? `?limit=${params.limit}` : '';
    return apiFetch<RunSummary[]>(`/admin/runs${q}`);
  },
  getRunDetail(runId: number) {
    return apiFetch<RunDetail>(`/admin/runs/${runId}`);
  },
  getNodeRuns(runId: number) {
    return apiFetch<NodeRun[]>(`/admin/runs/${runId}/node-runs`);
  },
  getArtifacts(runId: number) {
    return apiFetch<Artifact[]>(`/admin/runs/${runId}/artifacts`);
  },

  // ── Cleanup feedback ──
  submitFeedback(payload: { doc_id: string; category: string; comment?: string }) {
    return apiFetch<CleanupFeedback>('/admin/cleanup-feedback', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },
  listFeedback() {
    return apiFetch<CleanupFeedback[]>('/admin/cleanup-feedback');
  },
  async feedbackCategories() {
    const raw = await apiFetch<Record<string, number>>('/admin/cleanup-feedback/categories');
    const total = Object.values(raw).reduce((sum, n) => sum + n, 0);
    return { categories: raw, total } as CleanupFeedbackSummary;
  },
  deleteFeedback(feedbackId: number) {
    return apiFetch<{ status: string }>(`/admin/cleanup-feedback/${feedbackId}`, {
      method: 'DELETE',
    });
  },

  // ── Cleanup rules ──
  suggestRule(payload: { sample_markdown: string; observed_issues: string }) {
    return apiFetch<CleanupRuleSuggestion>('/admin/cleanup-rules/suggest', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },
  applyRule(payload: { yaml: string }) {
    return apiFetch<{ status: string }>('/admin/cleanup-rules/apply', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },
  removeRule(ruleName: string) {
    return apiFetch<{ status: string }>(`/admin/cleanup-rules/${encodeURIComponent(ruleName)}`, {
      method: 'DELETE',
    });
  },
  exportRules() {
    return apiFetch<{ rules: unknown }>('/admin/cleanup-rules/export');
  },
  importRules(payload: FormData) {
    return apiFetchRaw('/admin/cleanup-rules/import', {
      method: 'POST',
      body: payload,
    });
  },

  // ── Doc operations ──
  getDocActiveVersion(docId: string) {
    return apiFetch<DocActiveVersion>(`/admin/docs/${encodeURIComponent(docId)}/active-version`);
  },
  setDocActiveVersion(docId: string, version: string) {
    return apiFetch<{ ok: boolean }>(`/admin/docs/${encodeURIComponent(docId)}/active-version`, {
      method: 'POST',
      body: JSON.stringify({ doc_version: version }),
    });
  },
  deleteDoc(docId: string, params?: { tenant_id?: string; project_id?: string; corpus_id?: string }) {
    const q = new URLSearchParams();
    if (params?.tenant_id) q.set('tenant_id', params.tenant_id);
    if (params?.project_id) q.set('project_id', params.project_id);
    if (params?.corpus_id) q.set('corpus_id', params.corpus_id);
    const qs = q.toString();
    return apiFetch<{ status: string }>(`/admin/docs/${encodeURIComponent(docId)}${qs ? `?${qs}` : ''}`, {
      method: 'DELETE',
    });
  },
  exportDoc(docId: string, params?: { tenant_id?: string; project_id?: string; corpus_id?: string; format?: string }) {
    const q = new URLSearchParams();
    if (params?.tenant_id) q.set('tenant_id', params.tenant_id);
    if (params?.project_id) q.set('project_id', params.project_id);
    if (params?.corpus_id) q.set('corpus_id', params.corpus_id);
    if (params?.format) q.set('format', params.format);
    const qs = q.toString();
    return apiFetchRaw(`/admin/docs/${encodeURIComponent(docId)}/export${qs ? `?${qs}` : ''}`);
  },

  // ── Looking Glass ──
  lookingGlassQdrant() {
    return apiFetch<LookingGlassQdrant>('/admin/looking-glass/qdrant');
  },
  lookingGlassLedgerSummary() {
    return apiFetch<Record<string, unknown>>('/admin/looking-glass/ledger/summary');
  },
  lookingGlassInFlight() {
    return apiFetch<unknown[]>('/admin/looking-glass/ledger/in-flight');
  },
  lookingGlassFailures() {
    return apiFetch<Record<string, unknown>>('/admin/looking-glass/ledger/failures');
  },
  lookingGlassHitl() {
    return apiFetch<unknown[]>('/admin/looking-glass/ledger/hitl');
  },
  lookingGlassInventory() {
    return apiFetch<Record<string, unknown>>('/admin/looking-glass/inventory');
  },
  async lookingGlassDocs(params?: { corpus_id?: string }) {
    const q = params?.corpus_id ? `?corpus_id=${encodeURIComponent(params.corpus_id)}` : '';
    const resp = await apiFetch<{ docs: DocInfo[] }>(`/admin/looking-glass/docs${q}`);
    return resp.docs;
  },
  lookingGlassDocDetail(docId: string) {
    return apiFetch<Record<string, unknown>>(`/admin/looking-glass/docs/${encodeURIComponent(docId)}`);
  },
  lookingGlassChunkPreview(docId: string, chunkIndex: number) {
    return apiFetch<ChunkPreview>(`/admin/looking-glass/docs/${encodeURIComponent(docId)}/chunks/${chunkIndex}`);
  },
  lookingGlassMetrics() {
    return apiFetch<LookingGlassMetrics>('/admin/looking-glass/metrics');
  },

  // ── Bulk export / import ──
  exportCorpus(corpusId: string, params?: { tenant_id?: string; project_id?: string; format?: string }) {
    const q = new URLSearchParams();
    if (params?.tenant_id) q.set('tenant_id', params.tenant_id);
    if (params?.project_id) q.set('project_id', params.project_id);
    if (params?.format) q.set('format', params.format);
    const qs = q.toString();
    return apiFetchRaw(`/admin/corpora/${encodeURIComponent(corpusId)}/export${qs ? `?${qs}` : ''}`);
  },
  exportScoped(params?: { tenant_id?: string; project_id?: string; corpus_id?: string }) {
    const q = new URLSearchParams();
    if (params?.tenant_id) q.set('tenant_id', params.tenant_id);
    if (params?.project_id) q.set('project_id', params.project_id);
    if (params?.corpus_id) q.set('corpus_id', params.corpus_id);
    const qs = q.toString();
    return apiFetchRaw(`/admin/export${qs ? `?${qs}` : ''}`);
  },
  importCorpus(corpusId: string, payload: FormData) {
    return apiFetchRaw(`/admin/corpora/${encodeURIComponent(corpusId)}/import`, {
      method: 'POST',
      body: payload,
    });
  },

  // ── Self-test ──
  selfTest() {
    return apiFetch<Record<string, unknown>>('/admin/self-test', { method: 'POST' });
  },

  // ── Orphan maintenance ──
  scanOrphans(params?: { tenant_id?: string; project_id?: string; corpus_id?: string; max_points?: number }) {
    return apiFetch<OrphanScanResult>('/admin/maintenance/cleanup-orphan-chunks', {
      method: 'POST',
      body: JSON.stringify({
        dry_run: true,
        max_points: params?.max_points ?? 10000,
        tenant_id: params?.tenant_id || null,
        project_id: params?.project_id || null,
        corpus_id: params?.corpus_id || null,
      }),
    });
  },
  deleteOrphans(params?: { tenant_id?: string; project_id?: string; corpus_id?: string; max_points?: number }) {
    return apiFetch<OrphanScanResult>('/admin/maintenance/cleanup-orphan-chunks', {
      method: 'POST',
      body: JSON.stringify({
        dry_run: false,
        max_points: params?.max_points ?? 10000,
        tenant_id: params?.tenant_id || null,
        project_id: params?.project_id || null,
        corpus_id: params?.corpus_id || null,
      }),
    });
  },
  adoptOrphanGroup(payload: AdoptOrphanGroupRequest) {
    return apiFetch<AdoptOrphanGroupResult>('/admin/maintenance/adopt-orphan-group', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },
  reassociateRunScope(runId: number, payload: { tenant_id: string; project_id: string; corpus_id: string }) {
    return apiFetch<Record<string, unknown>>(`/admin/runs/${runId}/reassociate-scope`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },
  deleteDanglingRun(runId: number) {
    return apiFetch<{ ok: boolean; deleted_run_id: number; doc_id: string }>(
      `/admin/maintenance/dangling-run/${runId}`,
      { method: 'DELETE' },
    );
  },
};
