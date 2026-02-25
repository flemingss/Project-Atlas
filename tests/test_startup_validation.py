"""Tests for atlas.startup_validation._validate_admin_token and related helpers.

These tests call the internal validation functions directly with constructed
Settings objects — they do NOT go through the FastAPI app (which needs live
Postgres and Qdrant connections).
"""

from __future__ import annotations

import pytest

from atlas.settings import Settings
from atlas.startup_validation import _validate_admin_token


def _settings(**kwargs: str) -> Settings:
    """Build a Settings with env-file loading disabled and specified overrides."""
    return Settings.model_validate({"atlas_env": "dev", "atlas_admin_token": "", **kwargs})


def test_validate_admin_token_passes_in_dev() -> None:
    """ATLAS_ENV=dev with blank token should not raise."""
    settings = _settings(atlas_env="dev", atlas_admin_token="")
    _validate_admin_token(settings=settings)  # must not raise


def test_validate_admin_token_rejects_empty_in_prod() -> None:
    """ATLAS_ENV=prod with blank token should raise RuntimeError."""
    settings = _settings(atlas_env="prod", atlas_admin_token="")
    with pytest.raises(RuntimeError, match="ATLAS_ADMIN_TOKEN is required"):
        _validate_admin_token(settings=settings)


def test_validate_admin_token_rejects_placeholder_in_prod() -> None:
    """ATLAS_ENV=prod with ATLAS_ADMIN_TOKEN=change-me should raise RuntimeError."""
    settings = _settings(atlas_env="prod", atlas_admin_token="change-me")
    with pytest.raises(RuntimeError, match="placeholder"):
        _validate_admin_token(settings=settings)


def test_validate_admin_token_accepts_real_token_in_prod() -> None:
    """ATLAS_ENV=prod with a real secret should not raise."""
    settings = _settings(atlas_env="prod", atlas_admin_token="a-real-secret-token-xyz")
    _validate_admin_token(settings=settings)  # must not raise


def test_validate_paths_missing_config_dir(tmp_path) -> None:
    """Non-existent ATLAS_CONFIG_DIR should raise RuntimeError."""
    from atlas.startup_validation import _validate_paths

    settings = _settings(atlas_config_dir=str(tmp_path / "no_such_dir"))
    with pytest.raises(RuntimeError, match="ATLAS_CONFIG_DIR"):
        _validate_paths(settings=settings)


def test_validate_config_shapes_missing_version(tmp_path) -> None:
    """A pipeline.yaml without 'version:' should raise RuntimeError."""
    from atlas.config_manager import ConfigManager
    from atlas.startup_validation import _validate_config_shapes

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    # Write a pipeline.yaml without the version field
    (config_dir / "pipeline.yaml").write_text(
        "limits: { chunk_max_chars: 1000 }\n",
        encoding="utf-8",
    )
    (config_dir / "models.yaml").write_text(
        "version: 1\n"
        "providers: { deterministic: { type: deterministic } }\n"
        "roles: { embed_model: { provider: deterministic, model_name: m, params: {} } }\n",
        encoding="utf-8",
    )
    config_manager = ConfigManager(config_dir=config_dir)
    with pytest.raises(RuntimeError, match="version"):
        _validate_config_shapes(config_manager=config_manager)
