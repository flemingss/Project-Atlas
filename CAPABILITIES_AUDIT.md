# Project Atlas — Capabilities Audit

This document is the **central tracking location** for all capabilities advertised in Project Atlas documentation. For each capability it records the current implementation status, the primary code entry points (with GitHub permalinks), and the documentation source(s).

> **Status legend**
> - ✅ **Wired** — fully implemented and exercised by tests
> - 🟨 **Partial** — exists in code but behaviour is incomplete, placeholder, or will evolve
> - 🛠 **Deferred** — agreed direction; not yet implemented
> - ❓ **Unknown** — mentioned in docs but implementation status not confirmed

Base commit used for code permalinks: [`7bea9a6`](https://github.com/flemingss/Project-Atlas/commit/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6)

---

## 1. Core Service

| # | Capability | Status | Code Entry Point | Doc Source(s) |
|---|-----------|--------|-----------------|---------------|
| 1.1 | FastAPI app (health + root endpoints) | ✅ Wired | [`api.py:16`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/api.py#L16) | [README §Quickstart (Backend)](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/README.md), [TECHNICAL_DESIGN §3.1](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md#3-current-repo-implementation-truth-as-of-today) |
| 1.2 | Startup validation (env + config + fail-fast) | ✅ Wired | [`startup_validation.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/startup_validation.py) | [TECHNICAL_DESIGN Phase 1](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |
| 1.3 | Containerisation (Docker Compose stack) | ✅ Wired | [`docker-compose.yml`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/docker-compose.yml), [`Dockerfile`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/Dockerfile) | [README §Quickstart (Infra)](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/README.md), [TECHNICAL_DESIGN Phase 1](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |
| 1.4 | Admin token auth (`X-Atlas-Admin-Token`) | ✅ Wired | [`auth.py:11`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/auth.py#L11) | [README §Admin auth](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/README.md), [TECHNICAL_DESIGN Phase 1](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |
| 1.5 | Postgres schema creation via SQLAlchemy | ✅ Wired | [`db.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/db.py), [`db_init.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/db_init.py) | [TECHNICAL_DESIGN §3.1](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |

---

## 2. Configuration Management

| # | Capability | Status | Code Entry Point | Doc Source(s) |
|---|-----------|--------|-----------------|---------------|
| 2.1 | YAML-based config defaults (`pipeline.yaml`, `models.yaml`) | ✅ Wired | [`config_manager.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/config_manager.py), [`config/`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/config) | [README §Config & Tuning](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/README.md), [TECHNICAL_DESIGN §3.2](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |
| 2.2 | DB-backed config snapshots + activation | ✅ Wired | [`config_versions.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/config_versions.py) | [README §Tuning endpoints](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/README.md), [TECHNICAL_DESIGN §3.2](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |
| 2.3 | `GET /admin/config/effective` | ✅ Wired | [`api_admin.py:283`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/api_admin.py#L283) | [README §Admin / ops](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/README.md) |
| 2.4 | `POST /admin/reload-yaml` | ✅ Wired | [`api_admin.py:305`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/api_admin.py#L305) | [README §Admin / ops](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/README.md) |
| 2.5 | `GET/POST /admin/config-versions` + activate | ✅ Wired | [`api_admin.py:310`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/api_admin.py#L310) | [README §Tuning endpoints](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/README.md) |

---

## 3. RAG Endpoints

| # | Capability | Status | Code Entry Point | Doc Source(s) |
|---|-----------|--------|-----------------|---------------|
| 3.1 | `POST /rag/ingest/text` — pipeline-backed text ingest | ✅ Wired | [`api_rag.py:100`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/api_rag.py#L100), [`pipeline/runner.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/pipeline/runner.py) | [README §RAG MVP endpoints](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/README.md), [TECHNICAL_DESIGN §3.3](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |
| 3.2 | `POST /rag/ingest/file` — file ingest with optional Docling parse | ✅ Wired | [`api_rag.py:133`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/api_rag.py#L133), [`ingest/docling_adapter.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/ingest/docling_adapter.py) | [README §PDF/Office ingestion](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/README.md), [TECHNICAL_DESIGN §3.3](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md), [ARCHITECTURE §Pipeline Module](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/ARCHITECTURE.md) |
| 3.3 | `POST /rag/search` — vector search with tenant/project/finalized/fidelity filters | ✅ Wired | [`api_rag.py`](https://github.com/flemingss/Project-Atlas/blob/main/src/atlas/api_rag.py) | [README §RAG MVP endpoints](README.md), [TECHNICAL_DESIGN §3.3](TECHNICAL_DESIGN.md) |
| 3.3a | Fidelity mode search filter (`verified` / `verified+partial` / `all`) | ✅ Wired | [`api_rag.py`](https://github.com/flemingss/Project-Atlas/blob/main/src/atlas/api_rag.py) | [TECHNICAL_DESIGN §3.3](TECHNICAL_DESIGN.md), [ARCHITECTURE §Pipeline Module](ARCHITECTURE.md) |
| 3.4 | Multi-tenancy (`tenant_id` + `project_id`) isolation | ✅ Wired | [`api_rag.py:183`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/api_rag.py#L183), [`vectorstore/qdrant_store.py:59`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/vectorstore/qdrant_store.py#L59) | [TECHNICAL_DESIGN §3.3](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md), [HLD §5](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/HLD.md) |
| 3.5 | Hybrid search (BM25 + dense vector) | 🛠 Deferred | — | [TECHNICAL_DESIGN §7.1](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md), [HLD §3](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/HLD.md), [ARCHITECTURE §Next Steps](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/ARCHITECTURE.md) |
| 3.6 | Reranking (cross-encoder) | 🛠 Deferred | — | [TECHNICAL_DESIGN §7.1](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md), [HLD §3](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/HLD.md), [VALIDATION_REPORT §Known Limitations](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/VALIDATION_REPORT.md) |

---

## 4. Ingestion Pipeline

| # | Capability | Status | Code Entry Point | Doc Source(s) |
|---|-----------|--------|-----------------|---------------|
| 4.1 | Ingest node (document orchestration entry) | ✅ Wired | [`pipeline/ingest.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/pipeline/ingest.py), [`pipeline/orchestrator.py:52`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/pipeline/orchestrator.py#L52) | [TECHNICAL_DESIGN §3.6](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md), [ARCHITECTURE §Pipeline Module](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/ARCHITECTURE.md) |
| 4.2 | Docling PDF/Office parsing (optional dependency) | ✅ Wired | [`ingest/docling_adapter.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/ingest/docling_adapter.py) | [README §PDF/Office ingestion](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/README.md), [TECHNICAL_DESIGN Phase 4](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md), [HLD §2](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/HLD.md) |
| 4.3 | Judge node (multi-dimensional rubric: faithfulness/formatting/cohesion/hallucination_risk) | ✅ Wired | [`pipeline/judge.py`](https://github.com/flemingss/Project-Atlas/blob/main/src/atlas/pipeline/judge.py) | [TECHNICAL_DESIGN §3.6](TECHNICAL_DESIGN.md), [HLD §2](HLD.md), [ARCHITECTURE §Pipeline Module](ARCHITECTURE.md) |
| 4.3a | Cleanup node (deterministic markdown cleanup between Ingest and Judge) | ✅ Wired | [`pipeline/cleanup.py`](https://github.com/flemingss/Project-Atlas/blob/main/src/atlas/pipeline/cleanup.py) | [TECHNICAL_DESIGN §3.6](TECHNICAL_DESIGN.md), [ARCHITECTURE §Pipeline Module](ARCHITECTURE.md) |
| 4.3a-ii | Config-driven cleanup rules engine (6 step handlers, first-match-wins, rule-tag routing) | ✅ Wired | [`pipeline/cleanup_rules.py`](https://github.com/flemingss/Project-Atlas/blob/main/src/atlas/pipeline/cleanup_rules.py) | [TECHNICAL_DESIGN §3.6](TECHNICAL_DESIGN.md), [ARCHITECTURE §Pipeline Module](ARCHITECTURE.md) |
| 4.3b | Unified routing (`decide_next_step` — fail-fast, floor checks, cleanup-rejudge, rule-tag escalation) | ✅ Wired | [`pipeline/routing.py`](https://github.com/flemingss/Project-Atlas/blob/main/src/atlas/pipeline/routing.py) | [TECHNICAL_DESIGN §3.6](TECHNICAL_DESIGN.md), [ARCHITECTURE §Pipeline Module](ARCHITECTURE.md) |
| 4.3c | Retry/backoff for all external calls (LLM, vectorstore, Docling) | ✅ Wired | [`retry.py`](https://github.com/flemingss/Project-Atlas/blob/main/src/atlas/retry.py) | [TECHNICAL_DESIGN §3.6](TECHNICAL_DESIGN.md), [ARCHITECTURE §Retry / Backoff](ARCHITECTURE.md) |
| 4.3d | Chunk QA + fallback chain (semantic→paragraph, hierarchical→paragraph) | ✅ Wired | [`rag/chunk_qa.py`](https://github.com/flemingss/Project-Atlas/blob/main/src/atlas/rag/chunk_qa.py) | [TECHNICAL_DESIGN §3.6](TECHNICAL_DESIGN.md), [ARCHITECTURE §Chunk QA](ARCHITECTURE.md) |
| 4.3e | Docling health score (1–5 composite after every ingest) | ✅ Wired | [`ingest/docling_health.py`](https://github.com/flemingss/Project-Atlas/blob/main/src/atlas/ingest/docling_health.py) | [TECHNICAL_DESIGN §3.6](TECHNICAL_DESIGN.md), [ARCHITECTURE §Docling Health](ARCHITECTURE.md) |
| 4.4 | Refine node (retry ≤ 2 → HITL escalation) | ✅ Wired | [`pipeline/refine.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/pipeline/refine.py), [`pipeline/orchestrator.py:140`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/pipeline/orchestrator.py#L140) | [TECHNICAL_DESIGN §3.6](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md), [HLD §2](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/HLD.md) |
| 4.5 | Metadata node (tiered: tier-1 local / tier-2 frontier) | ✅ Wired | [`pipeline/metadata.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/pipeline/metadata.py), [`pipeline/orchestrator.py:155`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/pipeline/orchestrator.py#L155) | [TECHNICAL_DESIGN §3.6](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md), [HLD §2](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/HLD.md) |
| 4.6 | Paragraph chunking (`chunk_text`) | ✅ Wired | [`rag/chunking.py`](https://github.com/flemingss/Project-Atlas/blob/main/src/atlas/rag/chunking.py) | [TECHNICAL_DESIGN §3.3](TECHNICAL_DESIGN.md), [ARCHITECTURE §Enhanced Chunking](ARCHITECTURE.md) |
| 4.7 | Hierarchical (heading-aware) chunking (`chunk_text_hierarchical`) | ✅ Wired | [`rag/chunking.py`](https://github.com/flemingss/Project-Atlas/blob/main/src/atlas/rag/chunking.py) | [ARCHITECTURE §Enhanced Chunking](ARCHITECTURE.md), [HLD §2](HLD.md) |
| 4.7a | Semantic chunking (token-aware markdown-heading chunker, default strategy) | ✅ Wired | [`rag/chunking.py`](https://github.com/flemingss/Project-Atlas/blob/main/src/atlas/rag/chunking.py) | [TECHNICAL_DESIGN §3.3](TECHNICAL_DESIGN.md), [ARCHITECTURE §Enhanced Chunking](ARCHITECTURE.md) |
| 4.8 | Deterministic chunk IDs (content hash + doc_version) | ✅ Wired | [`pipeline/runner.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/pipeline/runner.py) | [TECHNICAL_DESIGN §3.3](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |
| 4.9 | Embeddings module (decoupled, traceable) | ✅ Wired | [`llm/provider.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/llm/provider.py), [`llm/registry.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/llm/registry.py) | [TECHNICAL_DESIGN §3.4](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md), [HLD §2](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/HLD.md) |
| 4.10 | Semantic cache (similarity ≥ 0.98 reuse) | 🛠 Deferred | — | [HLD §7](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/HLD.md), [VALIDATION_REPORT §Known Limitations](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/VALIDATION_REPORT.md) |

---

## 5. LLM Provider Abstraction

| # | Capability | Status | Code Entry Point | Doc Source(s) |
|---|-----------|--------|-----------------|---------------|
| 5.1 | `ILlmProvider` protocol (`chat()` + `embed()`) | ✅ Wired | [`llm/provider.py:14`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/llm/provider.py#L14) | [TECHNICAL_DESIGN §3.4](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |
| 5.2 | OpenAI-compatible provider (LM Studio / Ollama) | ✅ Wired | [`llm/openai_compat.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/llm/openai_compat.py) | [TECHNICAL_DESIGN §3.4](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md), [README §Local-only RAG](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/README.md) |
| 5.3 | Deterministic provider (CI-safe, no external LLM) | ✅ Wired | [`llm/deterministic.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/llm/deterministic.py), [`rag/deterministic.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/rag/deterministic.py) | [TECHNICAL_DESIGN §3.4](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md), [E2E_TEST_GUIDE §Deterministic Mode](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/E2E_TEST_GUIDE.md) |
| 5.4 | Frontier providers (OpenAI / Anthropic cloud) | 🛠 Deferred | [`llm/registry.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/llm/registry.py) (config shape only) | [TECHNICAL_DESIGN §3.4](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |
| 5.5 | Model registry (`ModelRegistry.resolve`) | ✅ Wired | [`llm/registry.py:25`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/llm/registry.py#L25) | [TECHNICAL_DESIGN §3.4](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |

---

## 6. Vector Store

| # | Capability | Status | Code Entry Point | Doc Source(s) |
|---|-----------|--------|-----------------|---------------|
| 6.1 | `QdrantStore` wrapper (upsert + search + ensure_collection) | ✅ Wired | [`vectorstore/qdrant_store.py:17`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/vectorstore/qdrant_store.py#L17) | [TECHNICAL_DESIGN §3.5](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |
| 6.2 | Dimension mismatch guard on existing collection | ✅ Wired | [`vectorstore/qdrant_store.py:32`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/vectorstore/qdrant_store.py#L32) | [TECHNICAL_DESIGN §3.5](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |
| 6.3 | `is_finalized` + `is_active_version` payload filters | ✅ Wired | [`vectorstore/qdrant_store.py:59`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/vectorstore/qdrant_store.py#L59) | [TECHNICAL_DESIGN §3.3](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |

---

## 7. Admin Endpoints

| # | Capability | Status | Code Entry Point | Doc Source(s) |
|---|-----------|--------|-----------------|---------------|
| 7.1 | `POST /admin/self-test` | ✅ Wired | [`api_admin.py:510`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/api_admin.py#L510) | [README §Release Candidate Verification](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/README.md), [TECHNICAL_DESIGN Phase 1](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |
| 7.2 | Workflow ledger (`GET /admin/runs`, `GET /admin/runs/{run_id}`) | ✅ Wired | [`api_admin.py:346`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/api_admin.py#L346), [`workflow_ledger.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/workflow_ledger.py) | [README §Admin / ops](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/README.md), [TECHNICAL_DESIGN Phase 2](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |
| 7.3 | HITL task API (`/admin/hitl/*`) | ✅ Wired | [`api_admin.py:402`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/api_admin.py#L402), [`hitl_ledger.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/hitl_ledger.py) | [README §HITL](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/README.md), [TECHNICAL_DESIGN §3.7](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |
| 7.4 | Looking Glass — Qdrant stats | ✅ Wired | [`api_admin.py:527`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/api_admin.py#L527) | [README §Looking Glass](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/README.md), [TECHNICAL_DESIGN §5.8](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |
| 7.5 | Looking Glass — corpus inventory | ✅ Wired | [`api_admin.py:602`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/api_admin.py#L602) | [README §Looking Glass](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/README.md), [TECHNICAL_DESIGN §5.8](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |
| 7.6 | Looking Glass — doc list + doc detail + chunk preview | ✅ Wired | [`api_admin.py:692`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/api_admin.py#L692) | [README §Looking Glass](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/README.md), [TECHNICAL_DESIGN §5.8](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |
| 7.7 | Looking Glass — ledger summary / in-flight / failures / HITL | ✅ Wired | [`api_admin.py:542`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/api_admin.py#L542) | [TECHNICAL_DESIGN Phase 2](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |
| 7.7a | Looking Glass — metrics aggregation (`GET /admin/looking-glass/metrics`) | ✅ Wired | [`api_admin.py`](https://github.com/flemingss/Project-Atlas/blob/main/src/atlas/api_admin.py) | [TECHNICAL_DESIGN §3.8](TECHNICAL_DESIGN.md), [ARCHITECTURE §Cleanup Feedback & Metrics](ARCHITECTURE.md) |
| 7.8 | Doc active-version management (`GET/POST /admin/docs/{doc_id}/active-version`) | ✅ Wired | [`api_admin.py:823`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/api_admin.py#L823), [`doc_versions.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/doc_versions.py) | [README §Doc versioning + export](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/README.md), [TECHNICAL_DESIGN Phase 5](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |
| 7.9 | Doc export (`GET /admin/docs/{doc_id}/export`) | ✅ Wired | [`api_admin.py:908`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/api_admin.py#L908), [`export_package.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/export_package.py) | [README §Doc versioning + export](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/README.md), [TECHNICAL_DESIGN Phase 5](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |
| 7.10 | Cleanup feedback API (`/admin/cleanup-feedback/*`) | ✅ Wired | [`api_admin.py`](https://github.com/flemingss/Project-Atlas/blob/main/src/atlas/api_admin.py), [`feedback_ledger.py`](https://github.com/flemingss/Project-Atlas/blob/main/src/atlas/feedback_ledger.py) | [TECHNICAL_DESIGN §3.8](TECHNICAL_DESIGN.md), [ARCHITECTURE §Cleanup Feedback & Metrics](ARCHITECTURE.md) |
| 7.11 | Rule suggestion API (`POST /admin/cleanup-rules/suggest`) | ✅ Wired | [`api_admin.py`](https://github.com/flemingss/Project-Atlas/blob/main/src/atlas/api_admin.py), [`rule_suggester.py`](https://github.com/flemingss/Project-Atlas/blob/main/src/atlas/rule_suggester.py) | [TECHNICAL_DESIGN §3.8](TECHNICAL_DESIGN.md), [ARCHITECTURE §Rule Suggester](ARCHITECTURE.md) |

---

## 8. HITL Workflow

| # | Capability | Status | Code Entry Point | Doc Source(s) |
|---|-----------|--------|-----------------|---------------|
| 8.1 | Postgres-backed HITL task lifecycle (pending → in_progress → completed/skipped/rejected) | ✅ Wired | [`hitl_ledger.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/hitl_ledger.py) | [TECHNICAL_DESIGN §3.7](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md), [ARCHITECTURE §HITL Management](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/ARCHITECTURE.md) |
| 8.2 | Priority queue formula `(10 − judge_score) × sensitivity_multiplier` | ✅ Wired | [`hitl.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/hitl.py) | [ARCHITECTURE §HITL Management](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/ARCHITECTURE.md), [HLD §5](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/HLD.md) |
| 8.3 | Pipeline resume after HITL resolution | ✅ Wired | [`api_admin.py:452`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/api_admin.py#L452) | [TECHNICAL_DESIGN §3.7](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md), [ARCHITECTURE §HITL Management](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/ARCHITECTURE.md) |
| 8.4 | Dify-based HITL UI | 🛠 Deferred | — | [TECHNICAL_DESIGN §4.1](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md), [HLD §5](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/HLD.md) |

---

## 9. Diagnostics & Observability

| # | Capability | Status | Code Entry Point | Doc Source(s) |
|---|-----------|--------|-----------------|---------------|
| 9.1 | Structured error codes (`DOC_PARSE_TIMEOUT`, `VLM_OCR_FAIL`, etc.) | ✅ Wired | [`diagnostics.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/diagnostics.py) | [ARCHITECTURE §Diagnostics Module](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/ARCHITECTURE.md), [HLD §5](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/HLD.md) |
| 9.2 | Trace levels (NONE / BASIC / DETAILED / FULL) | ✅ Wired | [`diagnostics.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/diagnostics.py) | [ARCHITECTURE §Diagnostics Module](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/ARCHITECTURE.md) |
| 9.3 | Durable workflow + node-run ledger in Postgres | ✅ Wired | [`workflow_ledger.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/workflow_ledger.py) | [TECHNICAL_DESIGN §3.8](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md), [TECHNICAL_DESIGN §7.3](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |
| 9.4 | Artifact store (filesystem + DB references) | ✅ Wired | [`artifacts.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/artifacts.py) | [TECHNICAL_DESIGN §7.3](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |
| 9.5 | Concurrency guard / vLLM semaphore | ✅ Wired | [`concurrency.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/concurrency.py) | [ARCHITECTURE §Concurrency Management](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/ARCHITECTURE.md), [HLD §4](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/HLD.md) |
| 9.6 | Privacy guard (`is_sensitive: true` default) | ✅ Wired | [`concurrency.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/concurrency.py) | [ARCHITECTURE §Concurrency Management](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/ARCHITECTURE.md), [HLD §4](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/HLD.md) |

---

## 10. Operator Console (Streamlit UI)

| # | Capability | Status | Code Entry Point | Doc Source(s) |
|---|-----------|--------|-----------------|---------------|
| 10.1 | Streamlit operator console | ✅ Wired | [`ui/app.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/ui/app.py) | [README §UI (Operator Console)](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/README.md), [OPTEST §Optional UI](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/OPTEST.md) |
| 10.2 | Purpose-built "Control Center" console (full HITL + monitoring pane) | 🛠 Deferred | — | [TECHNICAL_DESIGN §4.1](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md), [TECHNICAL_DESIGN §5.8](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |
| 10.3 | Cleanup & Tuning card (Admin tab) — rules viewer, feedback, metrics, AI rule suggestion | ✅ Wired | [`ui/app.py`](https://github.com/flemingss/Project-Atlas/blob/main/ui/app.py) | [TECHNICAL_DESIGN §3.10](TECHNICAL_DESIGN.md), [ARCHITECTURE §Cleanup & Tuning UI Card](ARCHITECTURE.md) |

---

## 11. Output / Export

| # | Capability | Status | Code Entry Point | Doc Source(s) |
|---|-----------|--------|-----------------|---------------|
| 11.1 | RAG package export ZIP (manifest + enriched markdown + index) | ✅ Wired | [`export_package.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/export_package.py), [`corpus_package.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/corpus_package.py) | [TECHNICAL_DESIGN Phase 5](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md), [HLD §6](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/HLD.md) |
| 11.2 | Rollback at `doc_version` granularity | ✅ Wired | [`doc_versions.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/doc_versions.py), [`api_admin.py:855`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/api_admin.py#L855) | [TECHNICAL_DESIGN Phase 5](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md), [TECHNICAL_DESIGN §7.4](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |
| 11.3 | Deep supersedes-chain semantics + grace-period hiding | 🛠 Deferred | — | [TECHNICAL_DESIGN §7.4](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md), [HLD §2](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/HLD.md) |

---

## 12. Testing Infrastructure

| # | Capability | Status | Code Entry Point | Doc Source(s) |
|---|-----------|--------|-----------------|---------------|
| 12.1 | Unit + integration tests (`pytest`) | ✅ Wired | [`tests/`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/tests) | [README §Tests](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/README.md), [E2E_TEST_GUIDE](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/E2E_TEST_GUIDE.md), [VALIDATION_REPORT](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/VALIDATION_REPORT.md) |
| 12.2 | Black-box E2E scenario runner (deterministic mode) | ✅ Wired | [`e2e/scenarios.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/e2e/scenarios.py), [`scripts/e2e_runner.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/scripts/e2e_runner.py) | [E2E_TEST_GUIDE](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/E2E_TEST_GUIDE.md), [OPTEST](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/OPTEST.md) |
| 12.3 | Local LLM E2E mode (Ollama / LM Studio) | ✅ Wired | [`e2e/scenarios.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/e2e/scenarios.py) | [E2E_TEST_GUIDE](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/E2E_TEST_GUIDE.md), [OPTEST](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/OPTEST.md) |
| 12.4 | Retrieval quality eval harness | ✅ Wired | [`eval/retrieval_eval.py`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/src/atlas/eval/retrieval_eval.py), [`eval/retrieval_golden.example.json`](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/eval/retrieval_golden.example.json) | [TECHNICAL_DESIGN Phase 6](https://github.com/flemingss/Project-Atlas/blob/7bea9a6f76ffdf258832eee9d7c6eb509e1ff6b6/TECHNICAL_DESIGN.md) |

---

## Summary

| Status | Count |
|--------|-------|
| ✅ Wired | 52 |
| 🟨 Partial | 0 |
| 🛠 Deferred | 7 |

**All previously Partial items have been wired.** v0.5.0 adds: config-driven cleanup rules engine (6 step handlers, rule-tag routing), cleanup feedback API (5 endpoints), metrics aggregation endpoint, CLEANUP→HITL transition. On top of v0.4.0: cleanup node, multi-dimensional judge (4 dimensions), unified routing, retry/backoff, chunk QA + fallback, Docling health score, fidelity mode search filter, semantic chunking as default strategy.

Test count: **265 passing** (up from 252 after 7A-7C, 208 at v0.4.0, 77 at initial audit, 128 pre-CR baseline).
