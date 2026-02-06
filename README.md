# Project Atlas

Local-first “Professional Grade RAG” pipeline (LangGraph) with HITL via Dify. Design source: `HLD.md`.

## Prereqs

- Windows + Docker Desktop
- Python (3.11+ recommended)
- LM Studio running an OpenAI-compatible server
  - Base URL: `http://192.168.20.113:1234` (swappable)

## Quickstart (Infra)

```powershell
docker compose up -d
```

This starts:
- Postgres on `localhost:5432`
- Qdrant on `localhost:6333`
- Redis on `localhost:6379`

And Dify (primary UI):
- Dify Web on `http://localhost` (port 80)
- Dify API on `http://localhost:5001`

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
- `GET /health`
- `GET /admin/config/effective`
- `POST /admin/reload-yaml`

Tuning endpoints (Postgres-backed config versions):
- `GET /admin/config-versions`
- `POST /admin/config-versions`
- `POST /admin/config-versions/{id}/activate`

RAG MVP endpoints (Qdrant-backed):
- `POST /rag/ingest/text`
- `POST /rag/search`

## Tests

Fast unit/breadcrumb tests (no Docker/LM Studio required):

```powershell
& ".\.venv\Scripts\python.exe" -m pytest -q
```

Integration breadcrumbs (hit live external services like Docker Qdrant):

```powershell
# Ensure docker compose is up and Qdrant is reachable at ATLAS_QDRANT_URL (defaults to http://localhost:6333)
& ".\.venv\Scripts\python.exe" -m pytest -m integration -q
```

## Dify

Dify is included in this repo’s `docker-compose.yml` for a unified local appliance baseline.

First-time setup:
- Open `http://localhost/install` to initialize the admin account.

Model provider:
- Configure Dify’s OpenAI-compatible provider to point at your LM Studio base URL (defaults to `ATLAS_OPENAI_BASE_URL` from `.env`).

## Config & Tuning

Defaults live in:
- `config/pipeline.yaml` (thresholds, caps, fallback toggles)
- `config/models.yaml` (model roles and provider wiring)

Goal: change models and thresholds without code edits by updating YAML and/or DB-stored config versions.
