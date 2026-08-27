#!/usr/bin/env bash
# Bootstrap the Atlas dev container. Idempotent — safe to re-run.
set -euo pipefail

cd /workspace

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }

say "Operator-local config (gitignored; stock defaults from *.example)"
for f in pipeline models; do
  if [ -f "config/${f}.yaml" ]; then
    echo "  config/${f}.yaml already exists — left alone"
  else
    cp "config/${f}.yaml.example" "config/${f}.yaml"
    echo "  created config/${f}.yaml"
  fi
done

if [ -f .env ]; then
  echo "  .env already exists — left alone"
else
  cp .env.example .env
  echo "  created .env from .env.example"
fi

mkdir -p artifacts
echo "  artifacts/ ready"

say "Python (editable install against /workspace/src)"
# Runtime deps are already baked into the image by the Dockerfile, so skip
# resolution and just relink the package to the bind-mounted source.
pip install --no-cache-dir --no-deps -e . -q
pip install --no-cache-dir -q pytest pytest-asyncio pytest-cov ruff
python -c "import atlas, sys; print('  atlas imported from', atlas.__file__)"

say "Frontend deps (web/)"
if [ -d web/node_modules ]; then
  echo "  web/node_modules present — skipping npm ci"
else
  # Non-fatal: the Docker build produces the UI bundle without this.
  # It only enables live Vite HMR (npm run dev).
  ( cd web && npm ci ) || echo "  npm ci failed — 'docker compose build atlas' still builds the UI"
fi

say "Ready"
cat <<'TIPS'
  Tests          pytest -q
  Lint           ruff check src tests
  Run API        python -m atlas          (or: uvicorn atlas.api:create_app --factory --host 0.0.0.0 --port 8080)
  Frontend HMR   cd web && npm run dev -- --host 0.0.0.0
  Reachable      postgres:5432 · qdrant:6333 · atlas:8080 · host.docker.internal:1234 (LM Studio)
TIPS
