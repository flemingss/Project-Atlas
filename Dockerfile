FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies
COPY pyproject.toml LICENSE README.md ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# Runtime assets (loaded from ATLAS_CONFIG_DIR, default ./config)
COPY config ./config
COPY scripts ./scripts

EXPOSE 8080

CMD ["python", "-m", "atlas"]
