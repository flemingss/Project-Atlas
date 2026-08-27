from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    atlas_env: str = "dev"
    atlas_log_level: str = "INFO"

    # Minimal RC auth: shared secret required for /admin endpoints in non-dev.
    atlas_admin_token: str = ""
    # Dev-only escape hatch to avoid auth friction in local bind-mount workflows.
    # Never enable in non-dev environments.
    atlas_dev_bypass_admin_auth: bool = False

    atlas_host: str = "127.0.0.1"
    atlas_port: int = 8080

    atlas_artifacts_dir: str = "./artifacts"

    # Directory containing pipeline.yaml and models.yaml. Defaults to repo-local ./config.
    atlas_config_dir: str = "./config"

    # OpenAI-compatible endpoint (LM Studio, etc). In Docker, prefer overriding
    # via ATLAS_OPENAI_BASE_URL (e.g. http://host.docker.internal:1234).
    # Only the 'local' profile depends on this; the 'api' profile reaches
    # OpenRouter and the embedding sidecar via their own provider base_urls.
    atlas_openai_base_url: str = "http://127.0.0.1:1234"

    # Active LLM profile — see config/models.yaml 'profiles'. Blank falls back
    # to models.yaml's active_profile. Switches model ids, context budget,
    # concurrency, and retry posture together.
    atlas_llm_profile: str = ""

    # Gateway credential for the 'api' profile. Named to match api_key_env in
    # models.yaml. Declared here (not just read from os.environ) so it resolves
    # when Atlas runs directly from .env rather than under docker compose.
    openrouter_api_key: str = ""

    # Embedding sidecar. Runs in the compose stack on CPU rather than on the
    # operator's machine, so ingest and search never depend on LM Studio being
    # up. Pinned across profiles — see atlas.llm.profiles.
    atlas_embeddings_base_url: str = "http://embeddings:80"


    atlas_default_tenant_id: str = "local"
    atlas_default_project_id: str = "default"
    atlas_default_corpus_id: str = "default"

    # Maintenance: periodic orphan Qdrant chunk cleanup.
    atlas_orphan_cleanup_enabled: bool = True
    atlas_orphan_cleanup_interval_s: int = 24 * 60 * 60
    atlas_orphan_cleanup_max_points: int = 5000
    atlas_orphan_cleanup_grace_hours: int = 48  # hours before auto-deleting orphan groups

    atlas_db_url: str = "postgresql+psycopg://atlas:atlas@localhost:5432/atlas"
    atlas_qdrant_url: str = "http://localhost:6333"

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

    # -------------------- Layout PDF parser --------------------
    # Backend selection: "auto" tries Docling first, layout fallback.
    # "auto_layout" tries layout first, Docling fallback.
    # "layout" forces layout parser only. "docling" forces Docling only.
    atlas_pdf_parser_backend: str = "auto"
    # Directory for ONNX models (layout, OCR, table structure).
    # Defaults to ./models/deepdoc; set ATLAS_MODELS_DIR to override.
    atlas_models_dir: str = "./models/deepdoc"
    # Minimum mean OCR confidence (0-1) to accept layout parser output.
    atlas_layout_ocr_confidence_min: float = 0.5
    # PDF zoom factor for page rendering (higher = better OCR, slower).
    atlas_layout_pdf_zoom: float = 3.0
