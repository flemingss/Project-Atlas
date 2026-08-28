from __future__ import annotations

import asyncio
import importlib.metadata
import logging
import shutil
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text

from atlas.api_admin import make_admin_router
from atlas.api_editor import make_editor_router
from atlas.api_rag import make_rag_router
from atlas.api_vlm_ingest import make_vlm_ingest_router
from atlas.config_manager import ConfigManager
from atlas.db import make_engine, make_sessionmaker
from atlas.db_init import ensure_schema
from atlas.logging_config import configure_logging
from atlas.models import WorkflowRun
from atlas.settings import Settings
from atlas.startup_validation import validate_startup

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Background maintenance
# ---------------------------------------------------------------------------

_MAINTENANCE_INTERVAL_S = 300  # 5 minutes


async def _maintenance_loop(
    settings: Settings,
    engine,
    vlm_registry,
) -> None:
    """Periodic background loop for lightweight self-maintenance.

    Runs every *_MAINTENANCE_INTERVAL_S* seconds and performs:
    - Releasing cold VLM ingest sessions from the in-memory cache (reclaims
      PDF bytes; durable state stays in the ledger, so nothing is lost).
    - Artifact directory age-based cleanup (keeps disk growth bounded).
    """
    artifact_max_age_s = 7 * 24 * 3600  # 7 days
    last_orphan_cleanup_ts = 0.0
    # Grace-period tracking: maps orphan key → first-seen timestamp.
    # Groups are only deleted after being orphaned for > grace_hours.
    orphan_first_seen: dict[tuple[str, str, str, str], float] = {}

    while True:
        await asyncio.sleep(_MAINTENANCE_INTERVAL_S)
        try:
            # ── VLM session eviction ──────────────────────────────
            if vlm_registry is not None:
                vlm_registry._evict_expired()

            # ── Artifact age-based cleanup ────────────────────────
            artifacts_root = Path(settings.atlas_artifacts_dir).resolve()
            runs_dir = artifacts_root / "runs"
            if runs_dir.is_dir():
                now = time.time()
                removed = 0
                for child in runs_dir.iterdir():
                    try:
                        age = now - child.stat().st_mtime
                        if age > artifact_max_age_s:
                            if child.is_dir():
                                shutil.rmtree(child, ignore_errors=True)
                            else:
                                child.unlink(missing_ok=True)
                            removed += 1
                    except OSError:
                        pass
                if removed:
                    log.info("Maintenance: purged %d artifact entries older than 7 days", removed)

            # ── Orphan Qdrant cleanup (daily, with grace period) ──
            if settings.atlas_orphan_cleanup_enabled:
                now_ts = time.time()
                interval_s = max(300, int(settings.atlas_orphan_cleanup_interval_s))
                grace_s = max(0, int(settings.atlas_orphan_cleanup_grace_hours)) * 3600
                if (now_ts - last_orphan_cleanup_ts) >= interval_s:
                    import httpx

                    with make_sessionmaker(engine)() as db_session:
                        rows = db_session.execute(
                            select(
                                WorkflowRun.tenant_id,
                                WorkflowRun.project_id,
                                WorkflowRun.doc_id,
                                WorkflowRun.doc_version,
                            )
                        ).all()
                    valid_keys = {
                        (str(t), str(p), str(d), str(v))
                        for t, p, d, v in rows
                    }

                    collection = "atlas_chunks"
                    max_points = max(100, min(int(settings.atlas_orphan_cleanup_max_points), 200000))
                    scanned = 0
                    next_offset: str | int | None = None
                    current_orphans: set[tuple[str, str, str, str]] = set()

                    async with httpx.AsyncClient(timeout=20.0) as client:
                        while scanned < max_points:
                            body: dict[str, object] = {
                                "limit": int(min(500, max_points - scanned)),
                                "with_payload": True,
                                "with_vector": False,
                            }
                            if next_offset is not None:
                                body["offset"] = next_offset

                            resp = await client.post(
                                f"{settings.atlas_qdrant_url.rstrip('/')}/collections/{collection}/points/scroll",
                                json=body,
                            )
                            if resp.status_code == 404:
                                # Collection not created yet (empty deployment,
                                # or flushed) — nothing to scan.
                                break
                            resp.raise_for_status()
                            result = (resp.json() or {}).get("result") or {}
                            points = result.get("points") or []
                            next_offset = result.get("next_page_offset")
                            if not points:
                                break

                            for point in points:
                                scanned += 1
                                payload = point.get("payload") or {}
                                tenant_id = payload.get("tenant_id")
                                project_id = payload.get("project_id")
                                doc_id = payload.get("doc_id")
                                doc_version = payload.get("doc_version")
                                if not tenant_id or not project_id or not doc_id or not doc_version:
                                    continue
                                key = (str(tenant_id), str(project_id), str(doc_id), str(doc_version))
                                if key not in valid_keys:
                                    current_orphans.add(key)

                            if next_offset is None:
                                break

                        # ── Grace period: track first-seen, prune re-adopted ──
                        # Record newly discovered orphans
                        for key in current_orphans:
                            if key not in orphan_first_seen:
                                orphan_first_seen[key] = now_ts
                        # Remove entries that are no longer orphaned (re-adopted / re-ingested)
                        for key in list(orphan_first_seen):
                            if key not in current_orphans:
                                del orphan_first_seen[key]

                        # Only delete groups that have exceeded the grace period
                        mature_orphans = {
                            key for key in current_orphans
                            if (now_ts - orphan_first_seen.get(key, now_ts)) >= grace_s
                        }

                        deleted = 0
                        for tenant_id, project_id, doc_id, doc_version in mature_orphans:
                            delete_payload = {
                                "filter": {
                                    "must": [
                                        {"key": "tenant_id", "match": {"value": tenant_id}},
                                        {"key": "project_id", "match": {"value": project_id}},
                                        {"key": "doc_id", "match": {"value": doc_id}},
                                        {"key": "doc_version", "match": {"value": doc_version}},
                                    ]
                                },
                                "wait": True,
                            }
                            del_resp = await client.post(
                                f"{settings.atlas_qdrant_url.rstrip('/')}/collections/{collection}/points/delete",
                                json=delete_payload,
                            )
                            del_resp.raise_for_status()
                            deleted += 1
                            # Clean up tracking entry after deletion
                            orphan_first_seen.pop((tenant_id, project_id, doc_id, doc_version), None)

                    if deleted or current_orphans:
                        log.info(
                            "Maintenance: orphan cleanup scanned=%d found=%d mature=%d deleted=%d grace_h=%d",
                            scanned,
                            len(current_orphans),
                            len(mature_orphans),
                            deleted,
                            settings.atlas_orphan_cleanup_grace_hours,
                        )
                    last_orphan_cleanup_ts = now_ts

        except Exception:
            log.exception("Maintenance loop error (non-fatal)")


# ---------------------------------------------------------------------------
# Health probes
# ---------------------------------------------------------------------------


def _check_postgres(engine) -> tuple[bool, str]:
    """Quick SELECT 1 against Postgres. Returns (ok, detail)."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "ok"
    except Exception as exc:
        return False, str(exc)[:120]


def _check_qdrant(settings: Settings) -> tuple[bool, str]:
    """GET /collections against Qdrant. Returns (ok, detail)."""
    try:
        import httpx

        resp = httpx.get(f"{settings.atlas_qdrant_url}/collections", timeout=3.0)
        if resp.status_code == 200:
            return True, "ok"
        return False, f"status {resp.status_code}"
    except Exception as exc:
        return False, str(exc)[:120]


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    settings = Settings()
    config_dir = Path(settings.atlas_config_dir).resolve()
    config_manager = ConfigManager(
        config_dir=config_dir, profile=settings.atlas_llm_profile or None
    )

    engine = make_engine(settings.atlas_db_url)
    session_factory = make_sessionmaker(engine)

    # Reference kept so the maintenance loop can release cold sessions.
    # Lazily populated after the VLM ingest router is built.
    _vlm_registry_ref: dict[str, object] = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # ── Startup ───────────────────────────────────────────
        # First, before anything that might want to report a problem: without
        # this every atlas.* log record is discarded under uvicorn.
        configure_logging(settings.atlas_log_level)

        validate_startup(settings=settings, config_manager=config_manager, engine=engine)
        ensure_schema(engine)

        # Reconcile runs orphaned by a crash/restart: Atlas is single-process,
        # so at startup nothing can legitimately be 'running'. Without this,
        # an interrupted pipeline run sits in 'running' forever and never
        # surfaces as a failure.
        try:
            with session_factory() as session:
                stale = session.execute(
                    select(WorkflowRun).where(WorkflowRun.status == "running")
                ).scalars().all()
                for w in stale:
                    w.status = "failed"
                    w.error_message = "interrupted by API restart"
                if stale:
                    session.commit()
                    log.warning(
                        "Startup: marked %d orphaned 'running' run(s) as failed", len(stale)
                    )
        except Exception:
            log.warning("Startup run reconciliation failed (non-fatal)", exc_info=True)

        log.info("Atlas startup complete (env=%s)", settings.atlas_env)

        # Launch maintenance loop
        maint = asyncio.create_task(
            _maintenance_loop(settings, engine, _vlm_registry_ref.get("reg")),
            name="atlas-maintenance",
        )

        yield  # ← app is running

        # ── Shutdown ──────────────────────────────────────────
        maint.cancel()
        try:
            await maint
        except asyncio.CancelledError:
            pass
        engine.dispose()
        log.info("Atlas shutdown: engine disposed, maintenance stopped")

    try:
        _pkg_version = importlib.metadata.version("project-atlas")
    except importlib.metadata.PackageNotFoundError:
        _pkg_version = "0.0.0-dev"
    app = FastAPI(title="Project Atlas", version=_pkg_version, lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict:
        pg_ok, pg_detail = _check_postgres(engine)
        qd_ok, qd_detail = _check_qdrant(settings)
        all_ok = pg_ok and qd_ok
        return {
            "status": "ok" if all_ok else "degraded",
            "env": settings.atlas_env,
            "postgres": {"ok": pg_ok, "detail": pg_detail},
            "qdrant": {"ok": qd_ok, "detail": qd_detail},
        }

    @app.get("/")
    async def root() -> dict:
        return {
            "name": "Project Atlas",
            "version": _pkg_version,
            "status": "ok",
            "health": "/health",
            "docs": "/docs",
            "rag": "/rag",
            "admin": "/admin",
        }

    app.include_router(make_admin_router(config_manager=config_manager, session_factory=session_factory))
    app.include_router(make_rag_router(config_manager=config_manager, session_factory=session_factory))
    app.include_router(make_editor_router(config_manager=config_manager, session_factory=session_factory))

    vlm_router = make_vlm_ingest_router(config_manager=config_manager, session_factory=session_factory)
    app.include_router(vlm_router)
    _vlm_registry_ref["reg"] = getattr(vlm_router, "_vlm_session_registry", None)

    # Serve the React SPA (built from web/ → static/app/) at /app
    app_static = Path(__file__).resolve().parent.parent.parent / "static" / "app"
    if app_static.is_dir():
        app_assets = app_static / "assets"
        if app_assets.is_dir():
            app.mount("/app/assets", StaticFiles(directory=str(app_assets)), name="app-assets")

        @app.get("/app")
        @app.get("/app/")
        @app.get("/app/{full_path:path}")
        async def app_spa(full_path: str = "") -> FileResponse:
            target = (app_static / full_path).resolve()
            try:
                target.relative_to(app_static.resolve())
            except ValueError:
                raise HTTPException(status_code=404, detail="Not Found")
            if full_path and target.is_file():
                return FileResponse(target)
            index_file = app_static / "index.html"
            if not index_file.is_file():
                raise HTTPException(status_code=404, detail="App build not found")
            return FileResponse(index_file)

    return app
