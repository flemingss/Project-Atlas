# ── Stage 1: Build React UI ──
FROM node:22-slim AS ui-build
WORKDIR /ui
COPY web/package.json web/package-lock.json* ./
RUN npm ci
COPY web/ .
RUN npm run build
# Output lands in /ui/../static/app → /static/app

# ── Stage 2: Python runtime ──
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Optional: install extras at build time.
# Docling is a required base dependency.
ARG ATLAS_PIP_EXTRAS=""

# ---------- OS libraries (cached unless base image changes) ----------
# Docling OCR can pull in OpenCV via rapidocr. The slim base image does not
# include the shared libraries OpenCV expects at runtime.
#
# git is here rather than in the devcontainer's common-utils feature: the
# features block does not reliably take effect for this compose-based setup
# (no nvm/zsh/git land in the container), whereas this layer always does.
# Without it the dev shell has no source control at all.
#
# docker-cli is the CLI only (no daemon). It talks to a filtered socket
# proxy declared in .devcontainer/docker-compose.devcontainer.yml, which is
# what makes `docker logs` available in here for troubleshooting the stack
# without handing the container root-equivalent access to the host.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        less \
        openssh-client \
        docker-cli \
        libgl1 \
        libglib2.0-0 \
        libx11-6 \
        libxcb1 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# ---------- Dependency layer (cached until the lock file changes) ----------
# Install from requirements.lock, NOT from pyproject.toml.
#
# CI installs the lock and runs the suite against it. When this layer resolved
# pyproject's ranges instead, the image could ship different versions from the
# ones CI verified — a green build said nothing about what actually shipped,
# and the gap widened silently over time. Same input here, same output there.
#
# The lock also carries `--extra-index-url .../whl/cpu`, so this pulls CPU
# torch (~200MB) rather than the CUDA bundle (~6GB) an unpinned resolve picks.
COPY pyproject.toml LICENSE README.md requirements.lock ./
RUN mkdir -p src/atlas && echo '__version__ = "0.0.0"' > src/atlas/__init__.py \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.lock \
    && if [ -n "$ATLAS_PIP_EXTRAS" ]; then pip install --no-cache-dir ".[${ATLAS_PIP_EXTRAS}]"; fi

# ---------- Runtime user ----------
# Created *before* the model download so the files it produces are owned by
# the user that has to read them at runtime.
#
# `atlas` (uid 1000) matches the dev host's owner (apex-admin), so writes to
# the bind-mounted ./artifacts land as the host user's files instead of root's.
# Environments where host uid 1000 is the wrong choice can override the user
# per service: `user: "501"` in compose, or `docker run --user 501`.
#
# The baked model cache has to live somewhere the runtime user can READ. It
# used to sit under /root, which is mode 700 — the non-root process could not
# open a single file in there, so every Docling parse tried to re-download
# and died with PermissionError on /root/.cache (found 2026-08-30, one day
# after USER atlas landed). Downloading *as* the runtime user puts the files
# in its home with the right owner from the start; no `chown -R` layer, which
# would copy the ~600MB cache into a second layer.
# chmod 755: useradd creates the home 0700, which would lock the cache away
# from any `user:` override exactly the way /root did.
RUN useradd --uid 1000 --create-home atlas \
    && chmod 755 /home/atlas \
    && mkdir -p /app/models /app/artifacts \
    && chown atlas:atlas /app/models /app/artifacts /tmp
ENV HOME=/home/atlas \
    HF_HOME=/home/atlas/.cache/huggingface \
    DOCLING_ARTIFACTS_PATH=/home/atlas/.cache/docling/models

# ---------- Parse model weights (cached with the dependency layer) ----------
# Without this, the FIRST PDF ingest of a fresh deployment downloads these at
# request time — minutes of added latency, and a hard failure wherever egress
# to the HF CDN is restricted. Bake them in instead:
# - Docling's layout + table (+ RapidOCR) models via Docling's own downloader
#   into $DOCLING_ARTIFACTS_PATH. Docling then loads them from disk and never
#   asks the Hub. A plain huggingface_hub snapshot_download into the HF cache
#   is NOT enough: it fetches `main`, Docling asks for a pinned revision, and
#   with no network the lookup fails ("cannot find the appropriate snapshot
#   folder for the specified revision") — the image only worked online
#   (found 2026-08-30 by running the smoke with --network none).
# - deepdoc's ONNX set into ./models/deepdoc, where atlas.ingest.model_manager
#   expects it (fallback layout parser).
# atlas.startup_validation warns at boot if either is missing or unreadable;
# .github/workflows/image.yml proves a parse with networking disabled.
USER atlas
RUN python -c "\
from pathlib import Path; \
from docling.utils.model_downloader import download_models; \
download_models(output_dir=Path('/home/atlas/.cache/docling/models'), \
    with_layout=True, with_tableformer=True, with_rapidocr=True, \
    with_code_formula=False, with_picture_classifier=False); \
from huggingface_hub import snapshot_download; \
snapshot_download('InfiniFlow/deepdoc', local_dir='/app/models/deepdoc', \
    allow_patterns=['layout.onnx', 'det.onnx', 'rec.onnx', 'ocr.res', 'tsr.onnx'])"
USER root

# ---------- Source layer (rebuilds only on code changes — fast) ----------
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

# Schema migrations. Required at startup: atlas.db_init runs `upgrade head`
# against these, so an image without them can only fall back to create_all and
# would silently stop applying schema changes.
COPY alembic.ini ./
COPY migrations ./migrations

# Runtime assets (loaded from ATLAS_CONFIG_DIR, default ./config)
COPY config ./config
# If live config files are absent (fresh clone), fall back to stock .example copies.
RUN cp -n config/pipeline.yaml.example config/pipeline.yaml 2>/dev/null || true \
 && cp -n config/models.yaml.example config/models.yaml 2>/dev/null || true
COPY scripts ./scripts
COPY static ./static
# Overlay the React SPA build output
COPY --from=ui-build /static/app ./static/app

# ---------- Drop to the runtime user ----------
# The `atlas` user and its model cache were set up above, before the model
# download. Everything COPY'd since is root-owned; umask 022 keeps /app
# world-readable, so the app stays loadable without opening up the baked
# files. /app/artifacts is usually a bind/volume mount over the atlas-owned
# dir created above; when it isn't (e.g. `docker run` with no -v), that
# ownership is what lets the first write succeed.
USER atlas

EXPOSE 8080

CMD ["python", "-m", "atlas"]
