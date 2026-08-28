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

# ---------- Dependency layer (cached until pyproject.toml changes) ----------
# Copy only the project metadata + a tiny stub so pip can resolve deps
# without needing the real source tree. This means editing files under src/
# does NOT trigger a full pip re-install of docling, onnxruntime, etc.
COPY pyproject.toml LICENSE README.md ./
RUN mkdir -p src/atlas && echo '__version__ = "0.0.0"' > src/atlas/__init__.py \
    && pip install --no-cache-dir --upgrade pip \
    && if [ -n "$ATLAS_PIP_EXTRAS" ]; then pip install --no-cache-dir ".[${ATLAS_PIP_EXTRAS}]"; else pip install --no-cache-dir .; fi

# ---------- Parse model weights (cached with the dependency layer) ----------
# Without this, the FIRST PDF ingest of a fresh deployment downloads these at
# request time — minutes of added latency, and a hard failure wherever egress
# to the HF CDN is restricted. Bake them in instead:
# - Docling layout (heron) + table models into the HF cache, where
#   docling.StandardPdfPipeline resolves them at runtime.
# - deepdoc's ONNX set into ./models/deepdoc, where atlas.ingest.model_manager
#   expects it (fallback layout parser).
RUN python -c "\
from huggingface_hub import snapshot_download; \
snapshot_download('docling-project/docling-layout-heron'); \
snapshot_download('docling-project/docling-models'); \
snapshot_download('InfiniFlow/deepdoc', local_dir='/app/models/deepdoc', \
    allow_patterns=['layout.onnx', 'det.onnx', 'rec.onnx', 'ocr.res', 'tsr.onnx'])"

# ---------- Source layer (rebuilds only on code changes — fast) ----------
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

# Runtime assets (loaded from ATLAS_CONFIG_DIR, default ./config)
COPY config ./config
# If live config files are absent (fresh clone), fall back to stock .example copies.
RUN cp -n config/pipeline.yaml.example config/pipeline.yaml 2>/dev/null || true \
 && cp -n config/models.yaml.example config/models.yaml 2>/dev/null || true
COPY scripts ./scripts
COPY static ./static
# Overlay the React SPA build output
COPY --from=ui-build /static/app ./static/app

EXPOSE 8080

CMD ["python", "-m", "atlas"]
