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

## Ports

The optest stack publishes Atlas on host port **18080** (`docker-compose.optest.yml`).
This is deliberately different from the main stack (`docker-compose.yml`), which
publishes Atlas on **28080** and the CPU embeddings sidecar on **18090** — 18080
is left free there so both stacks can run side by side.

Inside either compose network Atlas still listens on `8080`; the E2E containers
reach it as `http://atlas:8080`.

## UI

The React SPA is built into the Docker image and served at `/app` (port 18080 in the optest stack, 28080 in the main stack).
No separate UI service is needed.

## PDF ingest (Docling)

The optest stack builds the full image (`Dockerfile`), where Docling is a base
dependency and handles PDF/Office parsing.

Docling is **not** required by Atlas as a whole. `Dockerfile.slim` is an
explicitly supported Docling-free variant (see `BUILD_VARIANTS.md`): there PDFs
route through the VLM path — `pdf_parser.backend: vision`, served by
`VisionParser` in `src/atlas/pipeline/parsers.py`. The `auto` / `auto_layout`
backends fall back via `FallbackParser` to the deepdoc layout parser, which
needs `cv2` + `onnxruntime` — also absent from the slim image — so a slim
deployment should be configured for the `vision` backend.
