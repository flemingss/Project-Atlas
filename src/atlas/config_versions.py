from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from atlas.config_manager import EffectiveConfig, _stable_hash
from atlas.deep_merge import deep_merge
from atlas.models import ConfigVersion


class ConfigVersionCreateRequest(BaseModel):
    name: str = ""
    notes: str = ""

    base: Literal["current", "active", "yaml"] = "current"
    patch: dict[str, Any] = Field(default_factory=dict)

    activate: bool = True


class ConfigVersionResponse(BaseModel):
    id: int
    created_at: dt.datetime
    name: str
    notes: str
    is_active: bool
    config_hash: str


def get_active_config_version(session: Session) -> ConfigVersion | None:
    res = session.execute(select(ConfigVersion).where(ConfigVersion.is_active.is_(True)))
    return res.scalars().first()


def list_config_versions(session: Session) -> list[ConfigVersion]:
    res = session.execute(select(ConfigVersion).order_by(ConfigVersion.id.desc()))
    return list(res.scalars().all())


def activate_config_version(session: Session, *, config_id: int) -> None:
    session.execute(update(ConfigVersion).values(is_active=False))
    session.execute(update(ConfigVersion).where(ConfigVersion.id == config_id).values(is_active=True))
    session.commit()


def create_config_version(
    session: Session,
    *,
    req: ConfigVersionCreateRequest,
    yaml_defaults: EffectiveConfig,
) -> ConfigVersion:
    active = get_active_config_version(session)

    # Annotated because the "active" branches put an int (active.id) in here;
    # without it the first branch narrows the type to dict[str, str].
    base_payload: dict[str, Any]
    base_source: dict[str, Any]

    if req.base == "yaml":
        base_payload = {"pipeline": yaml_defaults.pipeline, "models": yaml_defaults.models}
        base_source = {"base": "yaml", "yaml_hash": yaml_defaults.hash}
    elif req.base == "active":
        if active is None:
            base_payload = {"pipeline": yaml_defaults.pipeline, "models": yaml_defaults.models}
            base_source = {"base": "yaml", "yaml_hash": yaml_defaults.hash}
        else:
            base_payload = active.payload
            base_source = {"base": "active", "active_id": active.id, "active_hash": active.config_hash}
    else:  # current
        if active is None:
            base_payload = {"pipeline": yaml_defaults.pipeline, "models": yaml_defaults.models}
            base_source = {"base": "yaml", "yaml_hash": yaml_defaults.hash}
        else:
            base_payload = active.payload
            base_source = {"base": "active", "active_id": active.id, "active_hash": active.config_hash}

    merged = deep_merge(base_payload, req.patch)
    config_hash = _stable_hash(merged)

    if req.activate:
        session.execute(update(ConfigVersion).values(is_active=False))

    row = ConfigVersion(
        name=req.name,
        notes=req.notes,
        is_active=req.activate,
        config_hash=config_hash,
        payload={
            **merged,
            "_meta": {
                "created_from": base_source,
                "created_at": dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z"),
            },
        },
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
