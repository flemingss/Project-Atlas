from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from atlas.api_admin import make_admin_router
from atlas.api_editor import make_editor_router
from atlas.api_rag import make_rag_router
from atlas.api_vlm_ingest import make_vlm_ingest_router
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

    @app.get("/")
    async def root() -> dict:
        return {
            "name": "Project Atlas",
            "status": "ok",
            "health": "/health",
            "docs": "/docs",
            "rag": "/rag",
            "admin": "/admin",
        }

    app.include_router(make_admin_router(config_manager=config_manager, session_factory=session_factory))
    app.include_router(make_rag_router(config_manager=config_manager, session_factory=session_factory))
    app.include_router(make_editor_router(config_manager=config_manager, session_factory=session_factory))
    app.include_router(make_vlm_ingest_router(config_manager=config_manager, session_factory=session_factory))

    # Serve the React SPA (built from web/ → static/editor/) at /editor
    editor_static = Path(__file__).resolve().parent.parent.parent / "static" / "editor"
    if editor_static.is_dir():
        # Compatibility for pre-base builds that reference root-level /assets and /atlas-icon.svg.
        assets_dir = editor_static / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="editor-assets-compat")

        @app.get("/atlas-icon.svg")
        async def editor_icon() -> FileResponse:
            icon = editor_static / "atlas-icon.svg"
            if not icon.is_file():
                raise HTTPException(status_code=404, detail="Not Found")
            return FileResponse(icon)

        @app.get("/editor")
        @app.get("/editor/")
        @app.get("/editor/{full_path:path}")
        async def editor_spa(full_path: str = "") -> FileResponse:
            # Serve real static files when present; otherwise fall back to index.html for SPA routes.
            target = (editor_static / full_path).resolve()
            try:
                target.relative_to(editor_static.resolve())
            except ValueError:
                raise HTTPException(status_code=404, detail="Not Found")

            if full_path and target.is_file():
                return FileResponse(target)

            index_file = editor_static / "index.html"
            if not index_file.is_file():
                raise HTTPException(status_code=404, detail="Editor build not found")
            return FileResponse(index_file)

    return app
