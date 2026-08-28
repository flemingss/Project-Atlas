# Project Atlas

Local-first RAG system with a running FastAPI service (admin + RAG MVP), config versioning, and a black-box E2E scenario suite.

Pipeline: **Ingest → Cleanup → Judge → Refine → Metadata → Embeddings → Chunking → Commit** (11 nodes). Features config-driven cleanup rules engine, cleanup feedback API, metrics aggregation, LLM-assisted rule suggestion, Cleanup & Tuning UI, multi-dimensional judge rubric with per-dimension rationale, rich judge-to-refine context injection (sub-scores + iteration context), score regression rollback, diminishing-returns detection, cleanup-rejudge cycle guard, refine content-safety guardrails (min_preservation_ratio), failed-refines-don't-burn-retries semantics, rich HITL task context with resume loop guard, retry/backoff on all external calls, chunk QA with automatic fallback, Docling health scoring, unified routing with fail-fast and rule-tag escalation, fidelity mode search filtering, and five configurable builtin extraction-artifact fixes.

Design source of truth: `TECHNICAL_DESIGN.md` (build-continuity plan; current reality vs target end-state). `ARCHITECTURE.md` covers the current system architecture. Remaining work: `docs/ACTION_ITEMS.md`.

## Prereqs

Two supported paths. Pick one.

**A. Dev container (recommended)** — nothing installed on the host but Docker.
- Docker Desktop
- VS Code + the Dev Containers extension

**B. Native Windows** — if you'd rather run the toolchain directly.
- Windows + Docker Desktop
- Python 3.11+
- Node 20+ (only for live Vite HMR; `docker compose build` builds the UI without it)
- Optional: LM Studio (or any OpenAI-compatible server) for non-deterministic embeddings/LLM calls

## Quickstart (Dev Container)

Open the repo in VS Code → **Reopen in Container**. That builds the full Atlas
image (Docling included), brings up Postgres and Qdrant on the same compose
network, and bootstraps operator-local files via `.devcontainer/post-create.sh`.

Inside the container:

```bash
pytest -q                      # tests
ruff check src tests           # lint
python -m atlas                # run the API on :8080 (published to host :28081)
cd web && npm run dev -- --host 0.0.0.0   # Vite HMR on :5173
```

Service names resolve on the compose network — `postgres:5432`, `qdrant:6333`,
`atlas:8080`. LM Studio on the Windows host is `host.docker.internal:1234`.

The long-running `atlas` API container is independent of your shell: restarting
one does not disturb the other.

### Docker access from inside the container

The dev container can inspect the stack without being given control of it. A
`docker-proxy` sidecar (`tecnativa/docker-socket-proxy`) holds the real
`/var/run/docker.sock` and re-exposes only read-only endpoints on the compose
network; `DOCKER_HOST` points the CLI at it.

```bash
docker ps                      # what is running
docker logs -f atlas-atlas-1   # tail the API
docker logs atlas-embeddings   # sidecar startup / model download
docker stats                   # live resource use
docker inspect atlas-qdrant
```

**Refused by design:** `exec`, `run`, `stop`/`start`/`restart`, `rm`, `build`,
`compose up`. `POST: 0` on the proxy blocks every mutating call, so a rogue
dependency or agent in here cannot start a privileged container and mount your
host filesystem — the reason the raw socket is not mounted directly.

Run lifecycle commands from the **Windows host**. They would not work correctly
from in here regardless: the daemon resolves bind-mount paths on the host, where
`/workspace` is `E:\`, so `docker compose up` would mount the wrong thing.

Note the CLI is installed in the shared `Dockerfile`, so the `atlas` API image
carries it too. That is deliberate — the `atlas` and `workspace` services build
from an identical spec so Docker's layer cache makes the second build nearly
free, and splitting them with a build ARG would forfeit that. The CLI is inert
in the API container: `DOCKER_HOST` is unset there, and `docker-proxy` only
exists in the devcontainer overlay, not the production compose file.


## Quickstart (Infra)

There are exactly **two supported stacks** — dev and prod. Both build from the
same `Dockerfile`.

**Dev** (bind-mounted source, admin-auth bypass, workspace shell + filtered
docker proxy). `.env` sets `COMPOSE_FILE` to the dev overlay chain, so from the
repo root this is simply:

```powershell
docker compose up -d --build
```

That is equivalent to:

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml -f .devcontainer/docker-compose.devcontainer.yml up -d --build
```

**Prod** (immutable image, no mounts, no dev bypass — requires `ATLAS_ADMIN_TOKEN`):

```powershell
docker compose -f docker-compose.yml up -d --build
```

In the dev stack, Atlas defaults to **no auto-reload** to keep in-memory VLM
ingest sessions stable while using the wizard. If you need backend live-reload
while coding, set `ATLAS_DEV_AUTO_RELOAD=true` before `docker compose up`.

If you set `ATLAS_ENV` to a non-dev value (e.g. `prod`), Atlas will refuse to start unless you also set `ATLAS_ADMIN_TOKEN`.

This starts:
- Postgres on `localhost:15432`
- Qdrant on `localhost:17333`
- Atlas API on `http://localhost:28080`

By default, this repo’s compose stack brings up the **baseline appliance** only.

### PDF/Office ingestion (Docling)

PDF/Office parsing uses Docling, which is part of the standard image (parse
models are baked in at build time). If PDF ingest fails because Docling is
missing, rebuild the image (`docker compose build atlas`).

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

Cleanup rule management:
- `POST /admin/cleanup-rules/apply`
- `DELETE /admin/cleanup-rules/{name}`
- `GET /admin/cleanup-rules/export`
- `POST /admin/cleanup-rules/import`

Config validation & restore:
- `POST /admin/config/validate-rules`
- `POST /admin/config/restore-stock`

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
- Editor/VLM ingest APIs that mutate state also use the same admin token header.
- SPA convenience: opening `/app` with `?token=<token>` auto-persists the token to browser localStorage (`atlas_admin_token`) for subsequent API calls.
- `X-Atlas-Admin-Token` is the **only** accepted credential. There is no bearer-token path: an `Authorization: Bearer <token>` header is silently ignored and the request is rejected with `403 Invalid admin token`.
- Dev-only bypass: set `ATLAS_DEV_BYPASS_ADMIN_AUTH=true` to keep local editor/admin flows unblocked even if `ATLAS_ADMIN_TOKEN` is set. Keep this **off** outside local dev.
- If `ATLAS_ENV` is non-dev, Atlas refuses to start unless `ATLAS_ADMIN_TOKEN` is set to a non-placeholder secret.

Tuning endpoints (Postgres-backed config versions):
- `GET /admin/config-versions`
- `POST /admin/config-versions`
- `POST /admin/config-versions/{id}/activate`

RAG MVP endpoints (Qdrant-backed):
- `POST /rag/ingest/text`
- `POST /rag/ingest/file`
- `POST /rag/search`

## UI (React SPA)

Atlas includes a full-featured React SPA served at `/app` that replaces the previous Streamlit operator console.

**Pages** (routes are relative to `/app`):

| Route | Page |
|-------|------|
| `/` (index) | Dashboard |
| `ingest` | Unified Ingest wizard (Docling, VLM, Import, Paste) |
| `library` | Library |
| `search` | Search |
| `review` | Review (HITL queue) |
| `doc/:docId` | Document Editor |
| `run/:runId` | Document Editor (by workflow run) |
| `admin/health` | Admin → Health |
| `admin/cleanup` | Admin → Cleanup & Tuning |
| `admin/groups` | Admin → Groups (CRUD for workspaces/tenants, projects, collections/corpora) |
| `admin/danger` | Admin → Danger Zone (DB reset, restore stock config) |

`upload` and `vlm-ingest` are kept as redirects to `ingest`; any unknown path
redirects to the Dashboard. `/app` is the only SPA mount — there is no `/editor`
route.

**Stack:** Vite 8, React 18, TypeScript, shadcn/ui, Tailwind CSS, Zustand, TanStack React Query.

```powershell
# Development (live reload, proxies API to :28080)
cd web
npm install
npm run dev        # http://localhost:5173

# Production build (outputs to static/app/)
npm run build
```

The Docker image builds the React app automatically (Node.js multi-stage).

Admin auth: if `ATLAS_ADMIN_TOKEN` is set, open `/app?token=<token>` once — the SPA stores it in `localStorage`.

See [`web/README.md`](web/README.md) for the full developer guide (directory structure, design tokens, adding components/pages).

## Backups

`artifacts/`, the Postgres ledger, and the Qdrant collection are the only
state Atlas cannot regenerate. Back all three up together:

```powershell
.\scripts\backup.ps1                      # -> backups\<timestamp>\
.\scripts\restore.ps1 -Dir backups\<ts>   # put it all back
```

Restore overwrites the current stores and bounces the API. Verified by a
full flush-then-restore cycle (identical point counts and search scores).

To empty the stores between testing rounds without touching containers,
schema, or the embeddings weight cache, use `.\scripts\flush.ps1`.

## Tests

Fast unit/breadcrumb tests (no Docker/LM Studio required):

```powershell
& ".\.venv\Scripts\python.exe" -m pytest -q
```

733 tests across 60 files (latest CI: 733 passed / 6 skipped). `pyproject.toml` sets
`addopts = "--cov=atlas.pipeline --cov-report=term-missing --cov-fail-under=80"`, so a
**partial** run (a single file, a marker filter, a `-k` selection) reports near-zero
coverage and exits non-zero even when every selected test passes. Append `--no-cov`
whenever you are not running the whole suite.

Integration breadcrumbs (hit live external services like Docker Qdrant):

```powershell
# Ensure docker compose is up and Qdrant is reachable at ATLAS_QDRANT_URL.
# With this compose file, host Qdrant is exposed at http://localhost:17333.
& ".\.venv\Scripts\python.exe" -m pytest -m integration -q --no-cov
```

E2E workflow tests (comprehensive pipeline validation with mocked services):

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/test_e2e_workflows.py -v --no-cov
```

## Release Candidate Verification

Suggested pre-RC checks:

```powershell
# Unit tests (fast)
& ".\.venv\Scripts\python.exe" -m pytest -q

# Integration tests (requires: docker compose up -d)
# --no-cov: a marker-filtered subset cannot meet the 80% coverage gate.
docker compose up -d
& ".\.venv\Scripts\python.exe" -m pytest -m integration -q --no-cov

# Appliance self-test (requires: API + Qdrant + Postgres running)
# If ATLAS_ADMIN_TOKEN is set, include the header.
curl -X POST http://127.0.0.1:28080/admin/self-test

# With admin token:
curl -H "X-Atlas-Admin-Token: $env:ATLAS_ADMIN_TOKEN" -X POST http://127.0.0.1:28080/admin/self-test

# Lint (dev extra)
& ".\.venv\Scripts\python.exe" -m ruff check src tests
```

## E2E Scenario Tests

Black-box E2E scenarios (`scripts/e2e_scenarios.py`) talk to a **running**
stack over HTTP. With the dev stack up, run them from the workspace container:

```powershell
docker exec atlas-workspace python scripts/e2e_scenarios.py --api-url http://atlas:8080 --qdrant-url http://qdrant:6333 --timeout 60
```

Notes:
- If the Qdrant collection already exists, the runner matches its vector dimension.
- The dedicated e2e/optest compose stacks were removed in the dev/prod
  consolidation; they predate the embeddings sidecar. See git history
  (`E2E_TEST_GUIDE.md`, `OPTEST.md`, `docker-compose.optest.yml`) if a CI
  harness is revived later.

You can also run the built-in self-test against a running appliance:

```powershell
curl -X POST http://127.0.0.1:28080/admin/self-test

# With admin token:
curl -H "X-Atlas-Admin-Token: $env:ATLAS_ADMIN_TOKEN" -X POST http://127.0.0.1:28080/admin/self-test
```

## Dify

Dify is included in this repo’s `docker-compose.yml` for experiments.

Status: Atlas does not rely on Dify for the current RC path; the HITL/ops UX direction is a purpose-built console (“Control Center”) per `TECHNICAL_DESIGN.md`.

First-time setup:
- Open `http://localhost/install` to initialize the admin account.

Model provider:
- Configure Dify’s OpenAI-compatible provider to point at your LM Studio base URL (defaults to `ATLAS_OPENAI_BASE_URL` from `.env`).

## LLM Profiles

Generation runs in one of two postures, selected by `ATLAS_LLM_PROFILE` (or
`active_profile` in `config/models.yaml`):

| Profile | Generation | Context budget | Refine |
|---------|-----------|----------------|--------|
| `api` (default) | OpenRouter, ZDR-enforced | 1M | Holistic up to ~114 pages |
| `local` | LM Studio / any LAN OpenAI-compatible server | 16k | Sectional above ~30 pages |

```bash
ATLAS_LLM_PROFILE=local docker compose up -d atlas   # switch postures
```

A profile is a patch over **both** `models.yaml` and `pipeline.yaml`. It moves
model ids *and* the tuning that has to travel with them — context budget,
section size, judge budget. Switching only the models would keep the pipeline
sectioning documents that now fit whole, which buys the cost of the migration
without the benefit.

There is no "hybrid" profile by design. To run one role somewhere else, override
that role — a third profile would only drift out of sync with the other two.

### Embeddings are not profile-switchable

`embed_model` is served by the **`embeddings` sidecar** (CPU, in the compose
stack) and is pinned across profiles. This is deliberate and enforced in code
(`atlas/llm/profiles.py`).

A vector search only returns meaningful results when the query and the documents
were embedded by the same model. Swapping the embedder under an existing corpus
corrupts retrieval — and when the replacement happens to have the same
dimension, it does so *silently*, because Qdrant cannot detect it. Changing the
embedding model means re-indexing everything.

Running embeddings in the stack rather than on the operator's machine is what
makes LM Studio genuinely optional: ingest and search never depend on it. CPU is
sufficient because embedding is a single forward pass over ≤400-token chunks —
no decode loop, no KV cache. Budget minutes, not seconds, for a 2000-page manual
(~2,300 chunks).

### Zero data retention

The `openrouter` provider sets `enforce_zdr: true`, which adds
`provider: {"zdr": true}` to **every** request body, restricting routing to
zero-data-retention endpoints.

This is deliberately redundant with the account-level policy. Because that policy
already binds, the per-request flag cannot narrow model availability further — but
it keeps the guarantee in version control where it is reviewable, and it survives
someone relaxing the account setting later.

If a model has no ZDR-compliant endpoint the request fails, and Atlas says so
explicitly rather than surfacing a bare 404 that looks like a bad model id. Set
`enforce_zdr: false` on the provider to fall back to account-level policy alone.

### Large documents

Judge and refine both embed the entire document in their prompt with no
truncation, so both have explicit budgets:

- **`limits.max_context_tokens`** — context budget for one holistic refine pass.
- **`roles.refine_model.max_output_tokens`** — the model's *response* ceiling.
  Refine emits a full rewrite, so the response is about as long as the input.
  This usually binds first: a model with a 1M context may cap responses at 48k.
  Both are checked in `atlas.pipeline.tokens.fits_in_context`; exceeding either
  takes the sectional path.
- **`limits.judge_max_context_tokens`** — above this a document **skips quality
  grading** rather than failing ingest. It is still chunked, embedded and
  searchable, just not graded or refined. The skip is logged and recorded in the
  judge result as `skipped-oversize`.

Setting `max_context_tokens` to a model's advertised context window while
ignoring the output ceiling is the trap this design closes: the request fits, the
response silently truncates, and the preservation guard then rejects the result
as dropped sections — which reads like a model quality problem rather than a
misconfiguration.

Holistic refinement has a hard ceiling around the refine model's output cap. No
model currently on the vetted list can rewrite a full 2000-page manual in one
pass; those take the sectional path.

### Read timeouts are not retried

A read timeout means the model accepted the request and was still generating.
Replaying it identically burns another full timeout window — with
`retry.llm.max_retries: 3` that is 4x the wall clock before failing anyway.
Connect failures, 429s and 5xx are still retried. Tune `timeouts.read_s` per
provider in `models.yaml`.

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
- `chunking.qa:` — post-chunk validation bounds (`min_chunk_count`, `max_token_ratio_limit`, `max_duplication_ratio`, `min_coverage_ratio`). Unknown keys are merged in and silently ignored, so a typo fails open.
- `judge_dim_floors:` — per-dimension minimum scores (faithfulness, formatting, cohesion, hallucination_risk)
- `fail_fast_score:` — composite score at or below which the pipeline fails immediately
- `cleanup_rejudge:` — toggle for re-running cleanup when formatting score is low but content is OK (default `true`, cycle-guarded to max 1)
- `cleanup_rules:` — declarative per-corpus/per-mime-type cleanup rules (8 step handlers — `strip_lines_matching`, `rewrite_pattern`, `strip_headers_footers`, `normalize_headings`, `fix_numbered_headings`, `merge_hardwrapped_paragraphs`, `fix_bullets`, `html_unescape` — plus rule tags for routing). Stock `pipeline.yaml` ships `cleanup_rules: []`; a worked reference rule set lives in `personal_configs/`.
- `builtin_cleanup:` — toggle individual builtin extraction-artifact fixes (`html_unescape`, `fix_ligatures`, `strip_zero_width_chars`, `strip_page_numbers`, `strip_repetitive_lines`)
- `pdf_parser:` — PDF parser backend selection (`auto` = Docling first → layout fallback, `auto_layout`, `layout`, `docling`)
- `refine_min_preservation_ratio:` — minimum output/input length ratio for refine (default 0.85; prevents content loss)

Goal: change models and thresholds without code edits by updating YAML and/or DB-stored config versions.

### RAG without LM Studio

Embeddings no longer depend on LM Studio. They are served by the `embeddings`
sidecar in the compose stack, so ingest and search work with LM Studio stopped
under either profile — see [Embeddings are not profile-switchable](#embeddings-are-not-profile-switchable).

```bash
docker compose up -d embeddings
curl -s localhost:18090/health
```

First boot downloads ~550MB of weights; they are cached in the
`atlas_embeddings_cache` volume, so restarts are fast. The healthcheck allows a
180s cold start.

For offline unit-test sanity checks (no sidecar at all), embeddings can be
switched to a stub provider:
- In `config/models.yaml`, set `roles.embed_model.provider: deterministic`
- Optionally set `roles.embed_model.params.dim` to control vector size (default: 384)

Do not point a real corpus at the deterministic provider — its vectors are not
meaningful, and anything ingested with it must be re-indexed.
