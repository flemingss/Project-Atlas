"""Tests for atlas.startup_validation._validate_admin_token and related helpers.

These tests call the internal validation functions directly with constructed
Settings objects — they do NOT go through the FastAPI app (which needs live
Postgres and Qdrant connections).
"""

from __future__ import annotations

import pytest

from atlas.settings import Settings
from atlas.startup_validation import (
    _validate_admin_token,
    _validate_builtin_cleanup,
    validate_cleanup_rules,
)


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


# ---------------------------------------------------------------------------
# Builtin cleanup validation
# ---------------------------------------------------------------------------


class TestValidateBuiltinCleanup:
    def test_none_is_valid(self) -> None:
        _validate_builtin_cleanup(None)  # no error

    def test_empty_dict_is_valid(self) -> None:
        _validate_builtin_cleanup({})  # no error

    def test_valid_booleans(self) -> None:
        _validate_builtin_cleanup({"html_unescape": True, "fix_ligatures": False})

    def test_non_dict_raises(self) -> None:
        with pytest.raises(RuntimeError, match="must be a mapping"):
            _validate_builtin_cleanup("not a dict")

    def test_non_bool_value_raises(self) -> None:
        with pytest.raises(RuntimeError, match="must be a boolean"):
            _validate_builtin_cleanup({"html_unescape": "yes"})


# ---------------------------------------------------------------------------
# Cleanup-rules schema validation
# ---------------------------------------------------------------------------


class TestValidateCleanupRules:
    """Unit tests for the cleanup_rules schema validator."""

    def test_empty_list_valid(self) -> None:
        assert validate_cleanup_rules([]) == []

    def test_none_valid(self) -> None:
        assert validate_cleanup_rules(None) == []

    def test_valid_rule(self) -> None:
        rules = [
            {
                "name": "strip_pdf_headers",
                "match": {"mime_type": "application/pdf"},
                "steps": [
                    {"kind": "strip_headers_footers", "first_n": 2, "patterns": [r"^Page \d+$"]},
                    {"kind": "normalize_headings"},
                ],
                "tags": ["auto_fix_only"],
            }
        ]
        assert validate_cleanup_rules(rules) == []

    def test_missing_name(self) -> None:
        errors = validate_cleanup_rules([{"steps": [{"kind": "fix_bullets"}]}])
        assert any("name" in e for e in errors)

    def test_invalid_name_chars(self) -> None:
        errors = validate_cleanup_rules([
            {"name": "has spaces!", "steps": [{"kind": "fix_bullets"}]}
        ])
        assert any("alphanumeric" in e for e in errors)

    def test_unknown_step_kind(self) -> None:
        errors = validate_cleanup_rules([
            {"name": "bad_kind", "steps": [{"kind": "not_real"}]}
        ])
        assert any("unknown kind" in e for e in errors)

    def test_bad_regex_pattern(self) -> None:
        errors = validate_cleanup_rules([
            {"name": "bad_regex", "steps": [{"kind": "strip_lines_matching", "pattern": "[invalid"}]}
        ])
        assert any("invalid regex" in e for e in errors)

    def test_bad_regex_in_patterns_list(self) -> None:
        errors = validate_cleanup_rules([
            {
                "name": "bad_patterns",
                "steps": [{"kind": "strip_headers_footers", "patterns": ["(unclosed"]}],
            }
        ])
        assert any("invalid regex" in e for e in errors)

    def test_unknown_match_keys(self) -> None:
        errors = validate_cleanup_rules([
            {"name": "unk_match", "match": {"bad_key": "x"}, "steps": [{"kind": "fix_bullets"}]}
        ])
        assert any("unknown keys" in e for e in errors)

    def test_missing_steps(self) -> None:
        errors = validate_cleanup_rules([{"name": "no_steps"}])
        assert any("steps" in e for e in errors)

    def test_not_a_list(self) -> None:
        errors = validate_cleanup_rules("not a list")
        assert any("must be a list" in e for e in errors)

    def test_catch_all_match_valid(self) -> None:
        """An empty match block (catch-all) should be valid."""
        rules = [
            {"name": "catch_all", "match": {}, "steps": [{"kind": "fix_bullets"}]}
        ]
        assert validate_cleanup_rules(rules) == []

    def test_string_step_valid(self) -> None:
        """Steps given as bare strings (kind only) should be valid."""
        rules = [
            {"name": "string_steps", "steps": ["normalize_headings", "fix_bullets"]}
        ]
        assert validate_cleanup_rules(rules) == []
