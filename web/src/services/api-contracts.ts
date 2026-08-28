/**
 * Backend API contract types — derived from the generated OpenAPI schema.
 *
 * `src/api-types.gen.ts` is produced from the running backend's
 * /openapi.json by `npm run gen:api` (regenerate whenever backend Pydantic
 * models change, and commit the result). Service modules alias their
 * request/response types from here, so any backend field rename breaks
 * `tsc` at build time instead of surfacing as a silent runtime mismatch —
 * the bug class this repo hit fifteen times before this file existed.
 *
 * Endpoints whose backend response is an untyped dict have no generated
 * schema; their hand-written types stay in the service modules, marked
 * with a comment.
 */
import type { components } from '@/api-types.gen';

export type Schemas = components['schemas'];

// ── RAG ───────────────────────────────────────────────────────────
export type IngestTextRequest = Schemas['IngestTextRequest'];
export type IngestTextResponse = Schemas['IngestTextResponse'];
export type SearchRequest = Schemas['SearchRequest'];
export type SearchHit = Schemas['SearchHit'];
export type SearchResponse = Schemas['SearchResponse'];

// ── Editor ────────────────────────────────────────────────────────
export type DocResolveResponse = Schemas['DocResolveResponse'];
export type PageInfoResponse = Schemas['PageInfoResponse'];
export type VisionRefineRequest = Schemas['VisionRefineRequest'];
export type VisionRefineResponse = Schemas['VisionRefineResponse'];
export type SaveMarkdownRequest = Schemas['SaveMarkdownRequest'];
export type SaveMarkdownResponse = Schemas['SaveMarkdownResponse'];
export type LlmRefineRequest = Schemas['LlmRefineRequest'];
export type LlmRefineResponse = Schemas['LlmRefineResponse'];
export type ReJudgeRequest = Schemas['ReJudgeRequest'];
export type ReJudgeResponse = Schemas['ReJudgeResponse'];

// ── VLM ingest ────────────────────────────────────────────────────
export type StartSessionRequest = Schemas['StartSessionRequest'];
export type StartSessionResponse = Schemas['StartSessionResponse'];
export type PageSettingsUpdate = Schemas['PageSettingsUpdate'];
export type UpdateConfigRequest = Schemas['UpdateConfigRequest'];
export type ProcessPageRequest = Schemas['ProcessPageRequest'];
export type ProcessPageResponse = Schemas['ProcessPageResponse'];
export type ProcessAllResponse = Schemas['ProcessAllResponse'];
export type StitchResponse = Schemas['StitchResponse'];
export type CommitRequest = Schemas['CommitRequest'];
export type CommitResponse = Schemas['CommitResponse'];
export type SessionSummary = Schemas['SessionSummary'];
export type ResumableSession = Schemas['ResumableSession'];
export type PageSummary = Schemas['PageSummary'];
export type ExportConfigResponse = Schemas['ExportConfigResponse'];

// ── HITL ──────────────────────────────────────────────────────────
export type HitlTaskResponse = Schemas['HitlTaskResponse'];
export type HitlTaskCompleteRequest = Schemas['HitlTaskCompleteRequest'];
export type HitlTaskSkipRequest = Schemas['HitlTaskSkipRequest'];
export type HitlTaskRejectRequest = Schemas['HitlTaskRejectRequest'];

// ── Admin ─────────────────────────────────────────────────────────
export type ResetDbRequest = Schemas['ResetDbRequest'];
export type TenantCreateRequest = Schemas['TenantCreateRequest'];
export type ProjectCreateRequest = Schemas['ProjectCreateRequest'];
export type CorpusCreateRequest = Schemas['CorpusCreateRequest'];
export type ConfigVersionCreateRequest = Schemas['ConfigVersionCreateRequest'];
export type ConfigVersionResponse = Schemas['ConfigVersionResponse'];
export type WorkflowRunResponse = Schemas['WorkflowRunResponse'];
export type NodeRunResponse = Schemas['NodeRunResponse'];
export type ArtifactRefResponse = Schemas['ArtifactRefResponse'];
export type FeedbackCreateRequest = Schemas['FeedbackCreateRequest'];
export type FeedbackResponse = Schemas['FeedbackResponse'];
export type RuleSuggestionRequest = Schemas['RuleSuggestionRequest'];
export type ApplyCleanupRuleRequest = Schemas['ApplyCleanupRuleRequest'];
export type ImportCleanupRulesRequest = Schemas['ImportCleanupRulesRequest'];
export type CleanupDryRunRequest = Schemas['CleanupDryRunRequest'];
export type CleanupOrphansRequest = Schemas['CleanupOrphansRequest'];
export type AdoptOrphanGroupRequest = Schemas['AdoptOrphanGroupRequest'];
export type ReassociateRunScopeRequest = Schemas['ReassociateRunScopeRequest'];
export type SetActiveDocVersionRequest = Schemas['SetActiveDocVersionRequest'];
