# Optest (containerized full-stack E2E)

This repo includes a fully containerized “optest” stack that boots Atlas + its dependencies and runs end-to-end scenarios from inside a container.

## Prereqs

- Docker Desktop (Compose v2)

## Deterministic E2E (CI-safe)

Runs the stack using deterministic providers (no external LLM required).

PowerShell:

- `./scripts/optest.ps1 -Mode deterministic`

Manual:

- `docker compose -f docker-compose.optest.yml --profile deterministic up --build --abort-on-container-exit --exit-code-from e2e`

## Local LLM E2E (exercises OpenAI-compatible server)

This uses Ollama’s OpenAI compatibility API and pulls small default models.

PowerShell:

- `./scripts/optest.ps1 -Mode local_llm`

Manual:

- `docker compose -f docker-compose.optest.yml --profile local_llm up --build --abort-on-container-exit --exit-code-from e2e-local-llm`

Model overrides (optional):

- `ATLAS_E2E_LLM_MODEL` (default: `llama3.2:1b`)
- `ATLAS_E2E_EMBED_MODEL` (default: `nomic-embed-text`)

Notes:

- First run may take a while while models download.
- Local-LLM scenarios activate an E2E config version so ingest calls actually hit the LLM + embeddings endpoints.

## LM Studio (host) + Dockerized Atlas

If LM Studio is running on your host, the containers should reach it via:

- `ATLAS_OPENAI_BASE_URL=http://host.docker.internal:<port>`

Then run:

- `./scripts/optest.ps1 -Mode lmstudio`

## UI\n\nThe React SPA is built into the Docker image and served at `/app` (port 18080).\nNo separate UI service is needed.

## PDF ingest (Docling)

Docling is a required dependency for PDF/Office parsing.
