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
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
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

EXPOSE 8080

CMD ["python", "-m", "atlas"]
