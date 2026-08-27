from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from atlas.admin._helpers import qdrant_collection, qdrant_post_json
from atlas.admin.cleanup_rules import register_cleanup_routes
from atlas.admin.config import register_config_routes
from atlas.admin.exports import register_export_routes
from atlas.admin.hitl import register_hitl_routes
from atlas.admin.looking_glass import register_looking_glass_routes
from atlas.admin.maintenance import register_maintenance_routes
from atlas.admin.scope import register_scope_routes
from atlas.admin.workflow import register_workflow_routes
from atlas.auth import require_admin_token, require_admin_token_strict
from atlas.config_manager import ConfigManager
from atlas.e2e.scenarios import run_scenarios
from atlas.settings import Settings
from atlas.vectorstore.qdrant_store import QdrantStore


class ResetDbRequest(BaseModel):
    confirm: str = ""
    postgres: bool = True
    qdrant: bool = True
    artifacts: bool = False


def make_admin_router(*, config_manager: ConfigManager, session_factory: sessionmaker[Session]) -> APIRouter:
    r = APIRouter(prefix="/admin", dependencies=[Depends(require_admin_token)])
    settings = Settings()

    # Best-effort: reuse the application's engine if present; otherwise create one.
    bind = None
    try:
        bind = getattr(session_factory, "kw", {}).get("bind")
    except Exception:
        bind = None

    if isinstance(bind, Engine):
        engine: Engine = bind
    else:
        from atlas.db import make_engine

        engine = make_engine(settings.atlas_db_url)

    # -- Qdrant / filesystem helpers (used only by db/reset) --

    async def _qdrant_clear_collection_points(*, collection: str) -> dict[str, Any]:
        import httpx

        try:
            return await qdrant_post_json(
                settings,
                f"/collections/{collection}/points/delete",
                {"filter": {}, "wait": True},
            )
        except httpx.HTTPStatusError as e:
            if e.response is not None and int(e.response.status_code) == 404:
                return {"ok": True, "detail": "collection not found"}
            raise

    def _reset_postgres_schema() -> None:
        from sqlalchemy import text

        from atlas.models import Base

        with engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                # Drop each table with CASCADE so FK constraints don't block the
                # drop order. sorted_tables puts parents first; reversed puts
                # dependents first -- the correct order for direct DROP.
                for table in reversed(Base.metadata.sorted_tables):
                    conn.execute(
                        text(f'DROP TABLE IF EXISTS "{table.name}" CASCADE')
                    )
            else:
                # SQLite (unit tests) does not support CASCADE; SQLAlchemy's
                # drop_all handles the ordering correctly for that dialect.
                Base.metadata.drop_all(conn)
            Base.metadata.create_all(conn)

    def _clear_artifacts_dir() -> int:
        import shutil
        from pathlib import Path

        root = Path(settings.atlas_artifacts_dir).resolve()
        if not root.exists():
            return 0

        removed = 0
        for child in root.iterdir():
            # Keep common placeholders.
            if child.name in {".gitkeep"}:
                continue
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink(missing_ok=True)
                removed += 1
            except Exception:
                # Best-effort; don't block DB reset on filesystem permissions.
                continue
        return removed

    @r.post("/db/reset", dependencies=[Depends(require_admin_token_strict)])
    async def reset_db(req: ResetDbRequest) -> dict[str, Any]:
        """Danger zone: clear state so the system can be re-imported fresh.

        Requires a strict admin token (even in dev) and an explicit confirmation string.
        """

        if (req.confirm or "").strip().upper() != "RESET":
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail="confirm must be 'RESET'")

        out: dict[str, Any] = {"ok": True, "postgres": None, "qdrant": None, "artifacts": None}

        if bool(req.postgres):
            await run_in_threadpool(_reset_postgres_schema)
            out["postgres"] = {"ok": True}
        else:
            out["postgres"] = {"ok": True, "skipped": True}

        if bool(req.qdrant):
            collection = qdrant_collection()
            res = await _qdrant_clear_collection_points(collection=collection)
            out["qdrant"] = {"ok": True, "collection": collection, "result": res.get("result"), "detail": res.get("detail")}
        else:
            out["qdrant"] = {"ok": True, "skipped": True}

        if bool(req.artifacts):
            removed = await run_in_threadpool(_clear_artifacts_dir)
            out["artifacts"] = {"ok": True, "removed_entries": int(removed)}
        else:
            out["artifacts"] = {"ok": True, "skipped": True}

        return out

    # -- Register sub-module routes -----------------------------------------------
    register_scope_routes(r, session_factory=session_factory, settings=settings)
    register_looking_glass_routes(r, session_factory=session_factory, settings=settings)
    register_cleanup_routes(r, session_factory=session_factory, config_manager=config_manager, settings=settings)
    register_config_routes(r, session_factory=session_factory, config_manager=config_manager, settings=settings)
    register_workflow_routes(r, session_factory=session_factory)
    register_hitl_routes(r, session_factory=session_factory, config_manager=config_manager)
    register_maintenance_routes(r, session_factory=session_factory, settings=settings, QdrantStore=QdrantStore)
    register_export_routes(r, session_factory=session_factory, config_manager=config_manager, settings=settings)

    @r.post("/self-test")
    def self_test(request: Request) -> dict[str, Any]:
        timeout_s = float(request.query_params.get("timeout_s", "20.0"))
        api_url = str(request.base_url).rstrip("/")
        incoming_token = request.headers.get("X-Atlas-Admin-Token")

        summary = run_scenarios(
            api_url=api_url,
            qdrant_url=settings.atlas_qdrant_url,
            collection=qdrant_collection(),
            timeout_s=float(timeout_s),
            admin_token=incoming_token or settings.atlas_admin_token or None,
        )
        return {
            "ok": bool(summary.ok),
            "results": [{"name": r.name, "ok": bool(r.ok), "detail": r.detail} for r in summary.results],
        }

    return r

