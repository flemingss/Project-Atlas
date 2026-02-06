from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    atlas_env: str = "dev"
    atlas_log_level: str = "INFO"

    atlas_host: str = "127.0.0.1"
    atlas_port: int = 8080

    atlas_artifacts_dir: str = "./artifacts"

    atlas_openai_base_url: str = "http://192.168.20.113:1234"

    atlas_default_tenant_id: str = "local"
    atlas_default_project_id: str = "default"

    atlas_db_url: str = "postgresql+psycopg://atlas:atlas@localhost:5432/atlas"
    atlas_qdrant_url: str = "http://localhost:6333"
    atlas_redis_url: str = "redis://localhost:6379/0"
