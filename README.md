# Project Atlas

Local-first RAG system with a running FastAPI service (admin + RAG MVP), config versioning, and a repeatable black-box E2E runner.

Pipeline: **Ingest → Cleanup → Judge → Refine → Metadata → Embeddings → Chunking → Commit** (11 nodes). Features config-driven cleanup rules engine, cleanup feedback API, metrics aggregation, LLM-assisted rule suggestion, Cleanup & Tuning UI, multi-dimensional judge rubric, retry/backoff on all external calls, chunk QA with automatic fallback, Docling health scoring, unified routing with fail-fast and rule-tag escalation, and fidelity mode search filtering.

Design source of truth: `TECHNICAL_DESIGN.md` (build-continuity plan; current reality vs target end-state). `HLD.md` is retained as historical original intent.

## Capabilities Audit

The authoritative checklist of all advertised capabilities (status, code entry points, and doc sources) is maintained in **[`CAPABILITIES_AUDIT.md`](CAPABILITIES_AUDIT.md)**.

## Prereqs

- Windows + Docker Desktop
- Python (3.11+ recommended)
- Optional: LM Studio (or any OpenAI-compatible server) for non-deterministic embeddings/LLM calls

## Quickstart (Infra)

```powershell
docker compose up -d
```

If you set `ATLAS_ENV` to a non-dev value (e.g. `prod`), Atlas will refuse to start unless you also set `ATLAS_ADMIN_TOKEN`.

This starts:
- Postgres on `localhost:5432`
- Qdrant on `localhost:6333`
- Atlas API on `http://localhost:18080`

By default, this repo’s compose stack brings up the **baseline appliance** only.

### PDF/Office ingestion (Docling)

PDF/Office parsing requires Docling.

If PDF ingest fails because Docling is missing, treat it as a build/deploy problem (the container should include Docling).

Optional / experimental (profile-gated) Dify stack:

```powershell
docker compose --profile dify up -d
```

This starts Redis on `localhost:6379` and Dify services (Web on `http://localhost`, API on `http://localhost:5001`).

## Quickstart (Backend)

```powershell
# In repo root
python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -U pip
pip install -e .

# Copy and edit env
copy .env.example .env

python -m atlas
```

Backend endpoints:
- `GET /` (service info)
- `GET /health`

Admin / ops:
- `GET /admin/config/effective`
- `POST /admin/reload-yaml`
- `POST /admin/self-test`
- `GET /admin/runs`
- `GET /admin/runs/{run_id}`

Looking Glass:
- `GET /admin/looking-glass/qdrant`
- `GET /admin/looking-glass/inventory`
- `GET /admin/looking-glass/docs`
- `GET /admin/looking-glass/docs/{doc_id}`
- `GET /admin/looking-glass/docs/{doc_id}/chunks/{chunk_index}`
- `GET /admin/looking-glass/metrics`

Cleanup feedback:
- `POST /admin/cleanup-feedback`
- `GET /admin/cleanup-feedback`
- `GET /admin/cleanup-feedback/categories`
- `GET /admin/cleanup-feedback/{id}`
- `DELETE /admin/cleanup-feedback/{id}`

Cleanup rule suggestion:
- `POST /admin/cleanup-rules/suggest`

HITL:
- `GET /admin/hitl/tasks`
- `POST /admin/hitl/tasks`
- `POST /admin/hitl/tasks/next`
- `GET /admin/hitl/tasks/{task_id}`
- `POST /admin/hitl/tasks/{task_id}/complete`
- `POST /admin/hitl/tasks/{task_id}/resume`
- `POST /admin/hitl/tasks/{task_id}/skip`
- `POST /admin/hitl/tasks/{task_id}/reject`

Doc versioning + export:
- `GET /admin/docs/{doc_id}/active-version`
- `POST /admin/docs/{doc_id}/active-version`
- `GET /admin/docs/{doc_id}/export`

Admin auth:
- If `ATLAS_ADMIN_TOKEN` is set, `/admin/*` requires header `X-Atlas-Admin-Token: <token>`.
- If `ATLAS_ENV` is non-dev, Atlas refuses to start unless `ATLAS_ADMIN_TOKEN` is set to a non-placeholder secret.

Tuning endpoints (Postgres-backed config versions):
- `GET /admin/config-versions`
- `POST /admin/config-versions`
- `POST /admin/config-versions/{id}/activate`

RAG MVP endpoints (Qdrant-backed):
- `POST /rag/ingest/text`
- `POST /rag/ingest/file`
- `POST /rag/search`

## UI (Operator Console)

Atlas includes a small optional Streamlit UI that overlays the existing API (no backend changes required).

```powershell
# Install UI extras
pip install -e .[ui]

# Run the console
streamlit run ui\app.py
```

The UI uses:
- API: `ATLAS_API_URL` (default `http://127.0.0.1:18080`)
- Admin auth: `ATLAS_ADMIN_TOKEN` (sent as header `X-Atlas-Admin-Token` for `/admin/*`)

Notes:
- The repo disables Streamlit usage stats by default via `.streamlit/config.toml` (reduces noisy browser console errors in restricted/offline networks).
- If something goes wrong, use the UI sidebar **Diagnostics → Download logs (json)**.

## Tests

**For comprehensive testing documentation, see [`E2E_TEST_GUIDE.md`](E2E_TEST_GUIDE.md).**

Fast unit/breadcrumb tests (no Docker/LM Studio required):

```powershell
& ".\.venv\Scripts\python.exe" -m pytest -q
```

Integration breadcrumbs (hit live external services like Docker Qdrant):

```powershell
# Ensure docker compose is up and Qdrant is reachable at ATLAS_QDRANT_URL (defaults to http://localhost:6333)
& ".\.venv\Scripts\python.exe" -m pytest -m integration -q
```

E2E workflow tests (comprehensive pipeline validation with mocked services):

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/test_e2e_workflows.py -v
```

## Release Candidate Verification

Suggested pre-RC checks:

```powershell
# Unit tests (fast)
& ".\.venv\Scripts\python.exe" -m pytest -q

# Integration tests (requires: docker compose up -d)
docker compose up -d
& ".\.venv\Scripts\python.exe" -m pytest -m integration -q

# Appliance self-test (requires: API + Qdrant + Postgres running)
# If ATLAS_ADMIN_TOKEN is set, include the header.
curl -X POST http://127.0.0.1:18080/admin/self-test

# With admin token:
curl -H "X-Atlas-Admin-Token: $env:ATLAS_ADMIN_TOKEN" -X POST http://127.0.0.1:18080/admin/self-test

# Lint (dev extra)
& ".\.venv\Scripts\python.exe" -m ruff check src tests
```

## E2E Scenario Tests

**For comprehensive testing documentation, see [`E2E_TEST_GUIDE.md`](E2E_TEST_GUIDE.md).**

Black-box E2E scenario runner (talks to a running API + Qdrant):

```powershell
docker compose -f docker-compose.e2e.yml up -d
& ".\.venv\Scripts\python.exe" -m atlas

# In a second terminal (scenarios only):
& ".\.venv\Scripts\python.exe" scripts\e2e_scenarios.py

# Or run the full orchestrated flow (docker + api + scenarios):
& ".\.venv\Scripts\python.exe" scripts\e2e_runner.py
```

**Deterministic mode** (CI-safe, uses mock LLM providers):

```powershell
# Dockerized full stack
docker compose -f docker-compose.optest.yml --profile deterministic up --abort-on-container-exit
```

**Local LLM mode** (validates real AI behavior with Ollama or LM Studio):

```powershell
# With Ollama (auto-pulls models)
docker compose -f docker-compose.optest.yml --profile local_llm up --abort-on-container-exit

# With LM Studio on host
docker compose -f docker-compose.optest.yml --profile lmstudio up --abort-on-container-exit
```

Notes:
- If LM Studio isn’t running, the runner will activate a deterministic embeddings config version automatically.
- If the Qdrant collection already exists, the runner matches its vector dimension.
- See `E2E_TEST_GUIDE.md` for detailed scenario descriptions and test strategies.

You can also run the built-in self-test against a running appliance:

```powershell
curl -X POST http://127.0.0.1:18080/admin/self-test

# With admin token:
curl -H "X-Atlas-Admin-Token: $env:ATLAS_ADMIN_TOKEN" -X POST http://127.0.0.1:18080/admin/self-test
```

## Dify

Dify is included in this repo’s `docker-compose.yml` for experiments.

Status: Atlas does not rely on Dify for the current RC path; the HITL/ops UX direction is a purpose-built console (“Control Center”) per `TECHNICAL_DESIGN.md`.

First-time setup:
- Open `http://localhost/install` to initialize the admin account.

Model provider:
- Configure Dify’s OpenAI-compatible provider to point at your LM Studio base URL (defaults to `ATLAS_OPENAI_BASE_URL` from `.env`).

## Config & Tuning

Stock defaults are shipped as `.example` files; live copies are operator-local:

| Stock reference (tracked in git)     | Live file (gitignored, operator-local) |
|--------------------------------------|----------------------------------------|
| `config/pipeline.yaml.example`       | `config/pipeline.yaml`                 |
| `config/models.yaml.example`         | `config/models.yaml`                   |

**First-time setup** — after cloning, copy the stock files to create your live config:
```bash
cp config/pipeline.yaml.example config/pipeline.yaml
cp config/models.yaml.example   config/models.yaml
```
Docker builds handle this automatically (the Dockerfile falls back to `.example` copies).

**Restoring stock config** — if local edits break the pipeline:
- **UI:** Admin → Danger Zone → *Restore stock config*
- **API:** `POST /admin/config/restore-stock` with `{"confirm": "RESTORE"}`
- **Manual:** Copy the `.example` file over the live file

**Pre-commit guardrail** — `scripts/pre_commit_config_check.py` blocks commits that accidentally stage the live config files. Wire it into `.git/hooks/pre-commit` or your CI.

Key `pipeline.yaml` sections:
- `retry:` — per-subsystem retry config (`llm`, `vectorstore`, `docling`) with `max_retries`, `base_delay_s`, `max_delay_s`
- `chunking.strategy:` — default chunking strategy (`semantic` | `paragraph` | `hierarchical`)
- `chunking.qa:` — post-chunk validation bounds (`min_tokens`, `max_tokens`, `min_chunks`)
- `judge_dim_floors:` — per-dimension minimum scores (faithfulness, formatting, cohesion, hallucination_risk)
- `fail_fast_score:` — composite score at or below which the pipeline fails immediately
- `cleanup_rejudge:` — toggle for re-running cleanup when formatting score is low but content is OK
- `cleanup_rules:` — declarative per-corpus/per-mime-type cleanup rules (6 step handlers, rule tags for routing)

Goal: change models and thresholds without code edits by updating YAML and/or DB-stored config versions.

### Local-only RAG (no LM Studio)

If you don’t have an OpenAI-compatible server running (e.g., LM Studio), RAG ingest/search will fail at embeddings.

For local sanity checks, you can switch embeddings to a deterministic local provider:
- In `config/models.yaml`, set `roles.embed_model.provider: deterministic`
- Optionally set `roles.embed_model.params.dim` to control vector size (default: 384)
