from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    atlas_env: str = "dev"
    atlas_log_level: str = "INFO"

    # Minimal RC auth: shared secret required for /admin endpoints in non-dev.
    atlas_admin_token: str = ""

    atlas_host: str = "127.0.0.1"
    atlas_port: int = 8080

    atlas_artifacts_dir: str = "./artifacts"

    # Directory containing pipeline.yaml and models.yaml. Defaults to repo-local ./config.
    atlas_config_dir: str = "./config"

    # OpenAI-compatible endpoint (LM Studio, etc). In Docker, prefer overriding
    # via ATLAS_OPENAI_BASE_URL (e.g. http://host.docker.internal:1234).
    atlas_openai_base_url: str = "http://127.0.0.1:1234"

    atlas_default_tenant_id: str = "local"
    atlas_default_project_id: str = "default"
    atlas_default_corpus_id: str = "default"

    atlas_db_url: str = "postgresql+psycopg://atlas:atlas@localhost:5432/atlas"
    atlas_qdrant_url: str = "http://localhost:6333"
    atlas_redis_url: str = "redis://localhost:6379/0"

    # -------------------- PDF ingest hardening --------------------
    # Guardrails
    atlas_pdf_max_bytes: int = 200 * 1024 * 1024  # 200MiB (aligns with common UI upload defaults)
    atlas_pdf_max_pages: int = 2000
    atlas_docling_timeout_s: float = 120.0

    # Quality gates (applied to PDF markdown output)
    atlas_pdf_quality_min_chars: int = 0
    atlas_pdf_quality_min_words: int = 0
    atlas_pdf_quality_alpha_ratio_min: float = 0.20
    atlas_pdf_quality_garbled_ratio_max: float = 0.02
