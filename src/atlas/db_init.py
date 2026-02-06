from __future__ import annotations

from sqlalchemy import Engine

from atlas.models import Base


def ensure_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        Base.metadata.create_all(conn)
