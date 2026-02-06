from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy.orm import Session, sessionmaker

from atlas.config_manager import ConfigManager
from atlas.config_versions import (
    ConfigVersionCreateRequest,
    ConfigVersionResponse,
    activate_config_version,
    create_config_version,
    get_active_config_version,
    list_config_versions,
)


def make_admin_router(*, config_manager: ConfigManager, session_factory: sessionmaker[Session]) -> APIRouter:
    r = APIRouter(prefix="/admin")

    @r.get("/config/effective")
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

    @r.post("/reload-yaml")
    def reload_yaml() -> dict:
        effective = config_manager.reload()
        return {"ok": True, "hash": effective.hash}

    @r.get("/config-versions", response_model=list[ConfigVersionResponse])
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

    @r.post("/config-versions", response_model=ConfigVersionResponse)
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

    @r.post("/config-versions/{config_id}/activate")
    def activate_version(config_id: int) -> dict:
        with session_factory() as session:
            activate_config_version(session, config_id=config_id)
        return {"ok": True, "active_id": config_id}

    return r
