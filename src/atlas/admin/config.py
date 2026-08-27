"""Configuration management routes (effective config, reload, restore, versions)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, sessionmaker

from atlas.auth import require_admin_token_strict
from atlas.config_manager import ConfigManager
from atlas.config_versions import (
    ConfigVersionCreateRequest,
    ConfigVersionResponse,
    activate_config_version,
    create_config_version,
    get_active_config_version,
    list_config_versions,
)
from atlas.settings import Settings


def register_config_routes(
    router: APIRouter,
    *,
    session_factory: sessionmaker[Session],
    config_manager: ConfigManager,
    settings: Settings,
) -> None:

    @router.get("/config/effective")
    def effective_config() -> dict:
        yaml_defaults = config_manager.get()
        with session_factory() as session:
            active = get_active_config_version(session)

        if active is None:
            return {
                "hash": yaml_defaults.hash,
                "pipeline": yaml_defaults.pipeline,
                "models": yaml_defaults.models,
                "source": {**yaml_defaults.source, "db": None},
            }

        payload = active.payload
        return {
            "hash": active.config_hash,
            "pipeline": payload.get("pipeline", {}),
            "models": payload.get("models", {}),
            "source": {**yaml_defaults.source, "db": {"active_id": active.id}},
        }

    @router.post("/reload-yaml")
    def reload_yaml() -> dict:
        effective = config_manager.reload()
        return {"ok": True, "hash": effective.hash}

    @router.post("/config/restore-stock", dependencies=[Depends(require_admin_token_strict)])
    def restore_stock_config(
        pipeline: bool = True,
        models: bool = True,
        confirm: str = "",
    ) -> dict[str, Any]:
        """Restore config YAML files from the shipped .example stock copies.

        Requires ``confirm == "RESTORE"`` to prevent accidental overwrites.
        After restoring, the config manager is reloaded automatically.
        """
        if (confirm or "").strip().upper() != "RESTORE":
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail="confirm must be 'RESTORE'")

        import shutil

        config_dir = Path(settings.atlas_config_dir)
        restored: list[str] = []
        errors: list[str] = []

        for name, should_restore in [("pipeline.yaml", pipeline), ("models.yaml", models)]:
            if not should_restore:
                continue
            example_path = config_dir / f"{name}.example"
            live_path = config_dir / name
            if not example_path.exists():
                errors.append(f"{name}.example not found at {example_path}")
                continue
            try:
                shutil.copy2(str(example_path), str(live_path))
                restored.append(name)
            except Exception as exc:
                errors.append(f"Failed to restore {name}: {exc}")

        # Reload so the running process picks up the restored files.
        if restored:
            effective = config_manager.reload()
            new_hash = effective.hash
        else:
            new_hash = config_manager.get().hash

        return {
            "ok": len(errors) == 0,
            "restored": restored,
            "errors": errors,
            "hash": new_hash,
        }

    @router.post("/config/validate-rules")
    def validate_rules_endpoint(rules: list[dict[str, Any]]) -> dict[str, Any]:
        """Validate a list of cleanup_rules entries without applying them.

        Returns ``{"valid": true, "errors": []}`` or ``{"valid": false, "errors": [...]}``
        """
        from atlas.startup_validation import validate_cleanup_rules

        errors = validate_cleanup_rules(rules)
        return {"valid": len(errors) == 0, "errors": errors}

    @router.get("/config-versions", response_model=list[ConfigVersionResponse])
    def config_versions() -> list[ConfigVersionResponse]:
        with session_factory() as session:
            rows = list_config_versions(session)
        return [
            ConfigVersionResponse(
                id=row.id,
                created_at=row.created_at,
                name=row.name,
                notes=row.notes,
                is_active=row.is_active,
                config_hash=row.config_hash,
            )
            for row in rows
        ]

    @router.post("/config-versions", response_model=ConfigVersionResponse)
    def create_version(req: ConfigVersionCreateRequest) -> ConfigVersionResponse:
        yaml_defaults = config_manager.get()
        with session_factory() as session:
            row = create_config_version(session, req=req, yaml_defaults=yaml_defaults)
        return ConfigVersionResponse(
            id=row.id,
            created_at=row.created_at,
            name=row.name,
            notes=row.notes,
            is_active=row.is_active,
            config_hash=row.config_hash,
        )

    @router.post("/config-versions/{config_id}/activate")
    def activate_version(config_id: int) -> dict:
        with session_factory() as session:
            activate_config_version(session, config_id=config_id)
        return {"ok": True, "active_id": config_id}
