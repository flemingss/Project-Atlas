FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Optional: install extras at build time.
# Docling is a required base dependency.
ARG ATLAS_PIP_EXTRAS=""

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

# Install dependencies
COPY pyproject.toml LICENSE README.md ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip \
    && if [ -n "$ATLAS_PIP_EXTRAS" ]; then pip install --no-cache-dir ".[${ATLAS_PIP_EXTRAS}]"; else pip install --no-cache-dir .; fi

# Runtime assets (loaded from ATLAS_CONFIG_DIR, default ./config)
COPY config ./config
COPY scripts ./scripts

EXPOSE 8080

CMD ["python", "-m", "atlas"]
