from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import Engine, text

from atlas.config_manager import ConfigManager
from atlas.retry import load_retry_configs
from atlas.settings import Settings


_DEV_ENVS = {"dev", "development", "local", "test"}
_log = logging.getLogger("atlas.startup")


def _is_non_dev(env: str) -> bool:
    return (env or "").strip().lower() not in _DEV_ENVS


def validate_startup(*, settings: Settings, config_manager: ConfigManager, engine: Engine) -> None:
    _validate_admin_token(settings=settings)
    _validate_paths(settings=settings)
    _validate_config_shapes(config_manager=config_manager)
    _validate_db_connection(settings=settings, engine=engine)
    _validate_qdrant(settings=settings)
    _warn_deterministic_config(settings=settings, engine=engine)


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

    # Load retry/backoff configuration from pipeline.yaml → retry section.
    load_retry_configs(pipeline.get("retry"))

    # Validate builtin_cleanup toggles (if present).
    _validate_builtin_cleanup(pipeline.get("builtin_cleanup"))

    # Validate cleanup_rules structure (if present).
    _validate_cleanup_rules(pipeline.get("cleanup_rules", []))


# ---------------------------------------------------------------------------
# Builtin cleanup validation
# ---------------------------------------------------------------------------

_VALID_BUILTIN_KEYS = frozenset({
    "html_unescape", "fix_ligatures", "strip_zero_width_chars",
    "strip_page_numbers", "strip_repetitive_lines",
    "repetitive_line_threshold", "repetitive_line_max_chars",
})


def _validate_builtin_cleanup(raw: Any) -> None:
    """Validate builtin_cleanup section — must be a mapping with boolean values."""
    if raw is None:
        return  # absent is fine — defaults to all-on
    if not isinstance(raw, dict):
        raise RuntimeError("pipeline.yaml builtin_cleanup must be a mapping")
    unknown = set(raw.keys()) - _VALID_BUILTIN_KEYS
    if unknown:
        _log.warning("builtin_cleanup: unknown keys %s (will be ignored)", sorted(unknown))
    # Boolean toggles
    _BOOL_KEYS = {"html_unescape", "fix_ligatures", "strip_zero_width_chars",
                  "strip_page_numbers", "strip_repetitive_lines"}
    for key in _BOOL_KEYS:
        val = raw.get(key)
        if val is not None and not isinstance(val, bool):
            raise RuntimeError(
                f"pipeline.yaml builtin_cleanup.{key} must be a boolean (got {type(val).__name__})"
            )
    # Integer parameters
    for key in ("repetitive_line_threshold", "repetitive_line_max_chars"):
        val = raw.get(key)
        if val is not None and not isinstance(val, int):
            raise RuntimeError(
                f"pipeline.yaml builtin_cleanup.{key} must be an integer (got {type(val).__name__})"
            )


# ---------------------------------------------------------------------------
# Cleanup-rules schema validation
# ---------------------------------------------------------------------------

_VALID_STEP_KINDS = frozenset({
    "strip_lines_matching",
    "rewrite_pattern",
    "strip_headers_footers",
    "normalize_headings",
    "merge_hardwrapped_paragraphs",
    "fix_bullets",
    "html_unescape",
})

_VALID_MATCH_KEYS = frozenset({
    "tenant_id", "project_id", "corpus_id", "mime_type", "filename_pattern",
})

_VALID_TAGS = frozenset({
    "auto_fix_only", "suspicious_content", "hard_failure",
})


def validate_cleanup_rules(raw: Any) -> list[str]:
    """Validate cleanup_rules structure; return list of human-readable errors.

    Public API — used by both startup validation and the restore-stock
    endpoint.  Returns an empty list when everything is valid.
    """
    errors: list[str] = []
    if raw is None or (isinstance(raw, list) and len(raw) == 0):
        return errors  # empty is fine
    if not isinstance(raw, list):
        errors.append("cleanup_rules must be a list")
        return errors

    for idx, entry in enumerate(raw):
        prefix = f"cleanup_rules[{idx}]"
        if not isinstance(entry, dict):
            errors.append(f"{prefix}: must be a mapping")
            continue

        # Name (required)
        name = entry.get("name")
        if not name or not isinstance(name, str):
            errors.append(f"{prefix}: 'name' is required and must be a non-empty string")
        elif not re.match(r"^[a-zA-Z0-9_-]+$", name):
            errors.append(f"{prefix}: 'name' must be alphanumeric/underscore/hyphen (got '{name}')")

        # Match block
        match_block = entry.get("match")
        if match_block is not None:
            if not isinstance(match_block, dict):
                errors.append(f"{prefix}: 'match' must be a mapping or omitted")
            else:
                unknown_keys = set(match_block.keys()) - _VALID_MATCH_KEYS
                if unknown_keys:
                    errors.append(f"{prefix}.match: unknown keys {sorted(unknown_keys)}")
                # Validate filename_pattern is parseable
                fp = match_block.get("filename_pattern")
                if fp is not None and not isinstance(fp, str):
                    errors.append(f"{prefix}.match.filename_pattern: must be a string")

        # Steps (required, non-empty)
        steps = entry.get("steps")
        if not steps or not isinstance(steps, list):
            errors.append(f"{prefix}: 'steps' is required and must be a non-empty list")
        else:
            for si, step in enumerate(steps):
                sp = f"{prefix}.steps[{si}]"
                if isinstance(step, str):
                    if step not in _VALID_STEP_KINDS:
                        errors.append(f"{sp}: unknown kind '{step}' (valid: {sorted(_VALID_STEP_KINDS)})")
                elif isinstance(step, dict):
                    kind = step.get("kind", "")
                    if kind not in _VALID_STEP_KINDS:
                        errors.append(f"{sp}: unknown kind '{kind}' (valid: {sorted(_VALID_STEP_KINDS)})")
                    # Validate regex patterns compile
                    for key in ("pattern",):
                        pat = step.get(key)
                        if pat is not None:
                            try:
                                re.compile(pat)
                            except re.error as exc:
                                errors.append(f"{sp}.{key}: invalid regex '{pat}' — {exc}")
                    # Validate patterns list in strip_headers_footers
                    patterns_list = step.get("patterns")
                    if patterns_list is not None:
                        if not isinstance(patterns_list, list):
                            errors.append(f"{sp}.patterns: must be a list")
                        else:
                            for pi, p in enumerate(patterns_list):
                                if not isinstance(p, str):
                                    errors.append(f"{sp}.patterns[{pi}]: must be a string")
                                else:
                                    try:
                                        re.compile(p)
                                    except re.error as exc:
                                        errors.append(f"{sp}.patterns[{pi}]: invalid regex '{p}' — {exc}")
                else:
                    errors.append(f"{sp}: must be a string or mapping")

        # Tags (optional)
        tags = entry.get("tags")
        if tags is not None:
            if not isinstance(tags, list):
                errors.append(f"{prefix}: 'tags' must be a list")
            else:
                unknown_tags = set(tags) - _VALID_TAGS
                if unknown_tags:
                    _log.warning("%s: non-standard tags %s (allowed but unusual)", prefix, sorted(unknown_tags))

    return errors


def _validate_cleanup_rules(raw: Any) -> None:
    """Startup gate: raise RuntimeError if cleanup_rules have structural problems."""
    errors = validate_cleanup_rules(raw)
    if errors:
        detail = "\n  - ".join(errors)
        raise RuntimeError(f"pipeline.yaml cleanup_rules validation failed:\n  - {detail}")


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


def _warn_deterministic_config(*, settings: Settings, engine: Engine) -> None:
    """Check if the active DB config version uses deterministic providers.

    This catches the common case where E2E tests activated deterministic
    (stub) providers and the teardown was skipped or failed, so real ingest
    would silently bypass external LLM calls.

    In non-dev environments this is promoted to an ERROR-level log.
    """
    try:
        from sqlalchemy.orm import Session
        from atlas.models import ConfigVersion
        from sqlalchemy import select

        with Session(engine) as session:
            active = session.execute(
                select(ConfigVersion).where(ConfigVersion.is_active.is_(True))
            ).scalars().first()
            if active is None:
                return  # No DB config override → YAML defaults will be used

            roles = (active.payload or {}).get("models", {}).get("roles", {})
            deterministic_roles = [
                role_name
                for role_name in ("judge_model", "refine_model", "embed_model",
                                  "metadata_tier1_model", "metadata_tier2_model")
                if (roles.get(role_name) or {}).get("provider") == "deterministic"
            ]
            if not deterministic_roles:
                return

            msg = (
                f"Active config version (id={active.id}) uses DETERMINISTIC provider for: "
                f"{', '.join(deterministic_roles)}. "
                "External LLM calls (e.g. LM Studio) will NOT be made during ingest. "
                "This is normal during E2E tests but unexpected in regular operation. "
                "To restore real providers: POST /admin/config-versions with "
                '{\"base\": \"yaml\", \"patch\": {}, \"activate\": true}'
            )
            if _is_non_dev(settings.atlas_env):
                _log.error(msg)
            else:
                _log.warning(msg)
    except Exception:  # noqa: BLE001
        # Best-effort check; don't block startup if the table doesn't exist yet.
        pass
