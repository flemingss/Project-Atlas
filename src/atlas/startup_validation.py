from __future__ import annotations

from pathlib import Path

import httpx
from sqlalchemy import Engine, text

from atlas.config_manager import ConfigManager
from atlas.settings import Settings


_DEV_ENVS = {"dev", "development", "local", "test"}


def _is_non_dev(env: str) -> bool:
    return (env or "").strip().lower() not in _DEV_ENVS


def validate_startup(*, settings: Settings, config_manager: ConfigManager, engine: Engine) -> None:
    _validate_admin_token(settings=settings)
    _validate_paths(settings=settings)
    _validate_config_shapes(config_manager=config_manager)
    _validate_db_connection(settings=settings, engine=engine)
    _validate_qdrant(settings=settings)


def _validate_admin_token(*, settings: Settings) -> None:
    if not _is_non_dev(settings.atlas_env):
        return

    token = (settings.atlas_admin_token or "").strip()
    if not token:
        raise RuntimeError(
            "ATLAS_ADMIN_TOKEN is required when ATLAS_ENV is non-dev. "
            "Set ATLAS_ADMIN_TOKEN (and send X-Atlas-Admin-Token on /admin requests)."
        )

    bad = {"change-me", "changeme", "sk-change-me", "default"}
    if token.lower() in bad:
        raise RuntimeError(
            "ATLAS_ADMIN_TOKEN is set to a placeholder value. "
            "Choose a real secret when ATLAS_ENV is non-dev."
        )


def _validate_paths(*, settings: Settings) -> None:
    config_dir = Path(settings.atlas_config_dir)
    if not config_dir.exists() or not config_dir.is_dir():
        raise RuntimeError(f"ATLAS_CONFIG_DIR does not exist or is not a directory: {config_dir}")

    pipeline_path = config_dir / "pipeline.yaml"
    models_path = config_dir / "models.yaml"
    if not pipeline_path.exists():
        raise RuntimeError(f"Missing config file: {pipeline_path}")
    if not models_path.exists():
        raise RuntimeError(f"Missing config file: {models_path}")

    artifacts_dir = Path(settings.atlas_artifacts_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)


def _validate_config_shapes(*, config_manager: ConfigManager) -> None:
    effective = config_manager.get()

    pipeline = effective.pipeline
    models = effective.models
    if not isinstance(pipeline, dict):
        raise RuntimeError("pipeline.yaml must be a YAML mapping")
    if not isinstance(models, dict):
        raise RuntimeError("models.yaml must be a YAML mapping")

    # Minimal sanity checks to catch common misconfigurations early.
    if "version" not in pipeline:
        raise RuntimeError("pipeline.yaml must contain a 'version' field")
    if "version" not in models:
        raise RuntimeError("models.yaml must contain a 'version' field")

    providers = models.get("providers")
    roles = models.get("roles")
    if not isinstance(providers, dict) or not providers:
        raise RuntimeError("models.yaml must contain a non-empty 'providers' mapping")
    if not isinstance(roles, dict) or not roles:
        raise RuntimeError("models.yaml must contain a non-empty 'roles' mapping")

    embed_role = roles.get("embed_model")
    if not isinstance(embed_role, dict):
        raise RuntimeError("models.yaml roles must include an 'embed_model' mapping")
    if not embed_role.get("provider"):
        raise RuntimeError("models.yaml roles.embed_model must set 'provider'")
    if not embed_role.get("model_name"):
        raise RuntimeError("models.yaml roles.embed_model must set 'model_name'")


def _validate_db_connection(*, settings: Settings, engine: Engine) -> None:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Database connection failed for ATLAS_DB_URL: {settings.atlas_db_url} ({e})") from e


def _validate_qdrant(*, settings: Settings) -> None:
    base = settings.atlas_qdrant_url.rstrip("/")
    try:
        with httpx.Client(timeout=3.0) as client:
            # Qdrant exposes /collections on all modern versions.
            r = client.get(f"{base}/collections")
            r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Qdrant unreachable at ATLAS_QDRANT_URL: {settings.atlas_qdrant_url} ({e})") from e
