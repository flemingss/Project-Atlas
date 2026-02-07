from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from atlas.api_admin import make_admin_router
from atlas.api_rag import make_rag_router
from atlas.config_manager import ConfigManager
from atlas.db import make_engine, make_sessionmaker
from atlas.db_init import ensure_schema
from atlas.settings import Settings
from atlas.startup_validation import validate_startup


def create_app() -> FastAPI:
    settings = Settings()
    config_dir = Path(settings.atlas_config_dir).resolve()
    config_manager = ConfigManager(config_dir=config_dir)

    engine = make_engine(settings.atlas_db_url)
    session_factory = make_sessionmaker(engine)

    app = FastAPI(title="Project Atlas", version="0.1.0")

    @app.on_event("startup")
    def _startup() -> None:
        validate_startup(settings=settings, config_manager=config_manager, engine=engine)
        ensure_schema(engine)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "env": settings.atlas_env}

    app.include_router(make_admin_router(config_manager=config_manager, session_factory=session_factory))
    app.include_router(make_rag_router(config_manager=config_manager, session_factory=session_factory))

    return app
