from __future__ import annotations

import io
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from qdrant_client.http import models as qm
from sqlalchemy.engine import Engine
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from atlas.admin._helpers import clean_scope_id, qdrant_collection, qdrant_post_json
from atlas.admin.cleanup_rules import register_cleanup_routes
from atlas.admin.looking_glass import register_looking_glass_routes
from atlas.admin.scope import register_scope_routes
from atlas.auth import require_admin_token, require_admin_token_strict
from atlas.config_manager import ConfigManager
from atlas.config_versions import (
    ConfigVersionCreateRequest,
    ConfigVersionResponse,
    activate_config_version,
    create_config_version,
    get_active_config_version,
    list_config_versions,
)
from atlas.e2e.scenarios import run_scenarios
from atlas.settings import Settings
from atlas.workflow_ledger import (
    ArtifactRefCreateRequest,
    ArtifactRefResponse,
    NodeRunCreateRequest,
    NodeRunResponse,
    WorkflowRunCreateRequest,
    WorkflowRunResponse,
    add_artifact_ref,
    create_node_run,
    create_workflow_run,
    get_workflow_run,
    list_artifact_refs,
    list_node_runs,
    list_workflow_runs,
    to_artifact_ref_response,
    to_node_run_response,
    to_run_response,
)
from atlas.hitl_ledger import (
    HitlTaskCompleteRequest,
    HitlTaskCreateRequest,
    HitlTaskRejectRequest,
    HitlTaskResponse,
    HitlTaskSkipRequest,
    claim_next_task,
    complete_task,
    create_hitl_task,
    get_hitl_task,
    list_hitl_tasks,
    reject_task,
    skip_task,
    to_hitl_response,
)
from atlas.models import ActiveDocVersion, HitlTaskRow, NodeRun, WorkflowRun
from atlas.pipeline.runner import resume_completed_hitl_task
from atlas.vectorstore.qdrant_store import QdrantStore


class ResetDbRequest(BaseModel):
    confirm: str = ""
    postgres: bool = True
    qdrant: bool = True
    artifacts: bool = False


class SetActiveDocVersionRequest(BaseModel):
    doc_version: str
    tenant_id: str | None = None
    project_id: str | None = None
    corpus_id: str | None = None


class ReassociateRunScopeRequest(BaseModel):
    tenant_id: str
    project_id: str
    corpus_id: str


class CleanupOrphansRequest(BaseModel):
    dry_run: bool = True
    max_points: int = 10000
    tenant_id: str | None = None
    project_id: str | None = None
    corpus_id: str | None = None


class AdoptOrphanGroupRequest(BaseModel):
    """Move orphaned Qdrant chunks to a valid scope and register a synthetic WorkflowRun."""
    old_tenant_id: str
    old_project_id: str
    old_doc_id: str
    old_doc_version: str
    tenant_id: str
    project_id: str
    corpus_id: str


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

    # ── Qdrant / filesystem helpers (used only by remaining routes) ──

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
        from atlas.models import Base
        from sqlalchemy import text

        with engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                # Drop each table with CASCADE so FK constraints don't block the
                # drop order. sorted_tables puts parents first; reversed puts
                # dependents first — the correct order for direct DROP.
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

    # ── Register sub-module routes ───────────────────────────────────
    register_scope_routes(r, session_factory=session_factory, settings=settings)
    register_looking_glass_routes(r, session_factory=session_factory, settings=settings)
    register_cleanup_routes(r, session_factory=session_factory, config_manager=config_manager, settings=settings)

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

    @r.post("/config/restore-stock", dependencies=[Depends(require_admin_token_strict)])
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

        from pathlib import Path
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

    @r.post("/config/validate-rules")
    def validate_rules_endpoint(rules: list[dict[str, Any]]) -> dict[str, Any]:
        """Validate a list of cleanup_rules entries without applying them.

        Returns ``{"valid": true, "errors": []}`` or ``{"valid": false, "errors": [...]}``
        """
        from atlas.startup_validation import validate_cleanup_rules

        errors = validate_cleanup_rules(rules)
        return {"valid": len(errors) == 0, "errors": errors}

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

    @r.get("/runs", response_model=list[WorkflowRunResponse])
    def runs(
        limit: int = Query(default=100, ge=1, le=500),
        tenant_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
    ) -> list[WorkflowRunResponse]:
        with session_factory() as session:
            rows = list_workflow_runs(session, limit=int(limit), tenant_id=tenant_id, project_id=project_id)
        return [to_run_response(r) for r in rows]

    @r.post("/runs", response_model=WorkflowRunResponse)
    def create_run(req: WorkflowRunCreateRequest) -> WorkflowRunResponse:
        with session_factory() as session:
            row = create_workflow_run(session, req=req)
        return to_run_response(row)

    @r.get("/runs/{run_id}", response_model=WorkflowRunResponse)
    def run_detail(run_id: int) -> WorkflowRunResponse:
        with session_factory() as session:
            row = get_workflow_run(session, run_id=run_id)
        if row is None:
            # Keep it simple for now; caller can treat as not found.
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="run not found")
        return to_run_response(row)

    @r.get("/runs/{run_id}/node-runs", response_model=list[NodeRunResponse])
    def node_runs(run_id: int, limit: int = Query(default=500, ge=1, le=2000)) -> list[NodeRunResponse]:
        with session_factory() as session:
            rows = list_node_runs(session, run_id=run_id, limit=int(limit))
        return [to_node_run_response(n) for n in rows]

    @r.post("/runs/{run_id}/node-runs", response_model=NodeRunResponse)
    def create_node_run_endpoint(run_id: int, req: NodeRunCreateRequest) -> NodeRunResponse:
        with session_factory() as session:
            # Validate run exists.
            if get_workflow_run(session, run_id=run_id) is None:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="run not found")
            row = create_node_run(session, run_id=run_id, req=req)
        return to_node_run_response(row)

    @r.get("/runs/{run_id}/artifacts", response_model=list[ArtifactRefResponse])
    def artifacts(run_id: int, limit: int = Query(default=500, ge=1, le=2000)) -> list[ArtifactRefResponse]:
        with session_factory() as session:
            rows = list_artifact_refs(session, run_id=run_id, limit=int(limit))
        return [to_artifact_ref_response(a) for a in rows]

    @r.post("/runs/{run_id}/artifacts", response_model=ArtifactRefResponse)
    def add_artifact(run_id: int, req: ArtifactRefCreateRequest) -> ArtifactRefResponse:
        with session_factory() as session:
            if get_workflow_run(session, run_id=run_id) is None:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="run not found")
            row = add_artifact_ref(session, run_id=run_id, req=req)
        return to_artifact_ref_response(row)

    @r.get("/hitl/tasks", response_model=list[HitlTaskResponse])
    def hitl_tasks(
        status: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        tenant_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
    ) -> list[HitlTaskResponse]:
        with session_factory() as session:
            rows = list_hitl_tasks(session, status=status, limit=int(limit), tenant_id=tenant_id, project_id=project_id)
        return [to_hitl_response(t) for t in rows]

    @r.post("/hitl/tasks", response_model=HitlTaskResponse)
    def create_hitl(req: HitlTaskCreateRequest) -> HitlTaskResponse:
        with session_factory() as session:
            if get_workflow_run(session, run_id=req.run_id) is None:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="run not found")
            row = create_hitl_task(session, req=req)
        return to_hitl_response(row)

    @r.get("/hitl/tasks/{task_id}", response_model=HitlTaskResponse)
    def hitl_task_detail(task_id: int) -> HitlTaskResponse:
        with session_factory() as session:
            row = get_hitl_task(session, task_id=task_id)
        if row is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="task not found")
        return to_hitl_response(row)

    @r.post("/hitl/tasks/next", response_model=HitlTaskResponse | None)
    def hitl_next(assigned_to: str = "") -> HitlTaskResponse | None:
        with session_factory() as session:
            row = claim_next_task(session, assigned_to=assigned_to)
        return None if row is None else to_hitl_response(row)

    @r.post("/hitl/tasks/{task_id}/complete", response_model=HitlTaskResponse)
    def hitl_complete(task_id: int, req: HitlTaskCompleteRequest) -> HitlTaskResponse:
        with session_factory() as session:
            try:
                row = complete_task(session, task_id=task_id, req=req)
            except KeyError:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="task not found")
            except ValueError as e:
                from fastapi import HTTPException

                raise HTTPException(status_code=409, detail=str(e)) from e
        return to_hitl_response(row)

    @r.post("/hitl/tasks/{task_id}/resume")
    async def hitl_resume(task_id: int) -> dict[str, Any]:
        try:
            res = await resume_completed_hitl_task(
                config_manager=config_manager,
                session_factory=session_factory,
                task_id=int(task_id),
            )
        except KeyError:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="task or run not found")
        except ValueError as e:
            from fastapi import HTTPException

            raise HTTPException(status_code=409, detail=str(e)) from e
        except Exception as e:  # noqa: BLE001
            from fastapi import HTTPException

            raise HTTPException(status_code=502, detail=str(e)) from e

        return {
            "ok": bool(res.get("ok")),
            "run_id": res.get("run_id"),
            "collection": res.get("collection"),
            "chunks_upserted": int(res.get("chunks_upserted", 0)),
            "paused_for_hitl": bool(res.get("paused_for_hitl", False)),
        }

    @r.post("/hitl/tasks/{task_id}/skip", response_model=HitlTaskResponse)
    def hitl_skip(task_id: int, req: HitlTaskSkipRequest) -> HitlTaskResponse:
        with session_factory() as session:
            try:
                row = skip_task(session, task_id=task_id, req=req)
            except KeyError:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="task not found")
            except ValueError as e:
                from fastapi import HTTPException

                raise HTTPException(status_code=409, detail=str(e)) from e
        return to_hitl_response(row)

    @r.post("/hitl/tasks/{task_id}/reject", response_model=HitlTaskResponse)
    def hitl_reject(task_id: int, req: HitlTaskRejectRequest) -> HitlTaskResponse:
        with session_factory() as session:
            try:
                row = reject_task(session, task_id=task_id, req=req)
            except KeyError:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="task not found")
            except ValueError as e:
                from fastapi import HTTPException

                raise HTTPException(status_code=409, detail=str(e)) from e
        return to_hitl_response(row)

    @r.post("/self-test")
    def self_test(request: Request, timeout_s: float = Query(default=20.0, ge=1.0, le=120.0)) -> dict[str, Any]:
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

    @r.get("/docs/{doc_id}/active-version")
    def doc_active_version(
        doc_id: str,
        tenant_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
        corpus_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        from atlas.doc_versions import get_active_doc_version, get_latest_doc_version_from_runs

        t_id = tenant_id or settings.atlas_default_tenant_id
        p_id = project_id or settings.atlas_default_project_id
        c_id = corpus_id or settings.atlas_default_corpus_id

        with session_factory() as session:
            active = get_active_doc_version(
                session,
                tenant_id=t_id,
                project_id=p_id,
                doc_id=doc_id,
                corpus_id=c_id,
            )
            latest = get_latest_doc_version_from_runs(session, tenant_id=t_id, project_id=p_id, doc_id=doc_id)

        return {
            "tenant_id": t_id,
            "project_id": p_id,
            "corpus_id": c_id,
            "doc_id": doc_id,
            "active_doc_version": active,
            "latest_doc_version": latest,
        }

    @r.post("/docs/{doc_id}/active-version")
    async def set_doc_active_version(doc_id: str, req: SetActiveDocVersionRequest) -> dict[str, Any]:
        from atlas.doc_versions import set_active_doc_version

        t_id = req.tenant_id or settings.atlas_default_tenant_id
        p_id = req.project_id or settings.atlas_default_project_id
        c_id = req.corpus_id or settings.atlas_default_corpus_id
        version = str(req.doc_version)

        with session_factory() as session:
            row = set_active_doc_version(
                session,
                tenant_id=t_id,
                project_id=p_id,
                doc_id=doc_id,
                doc_version=version,
                corpus_id=c_id,
            )

        # Best-effort: update Qdrant payload flags for global search filtering.
        store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection=qdrant_collection())
        base_must = [
            qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=t_id)),
            qm.FieldCondition(key="project_id", match=qm.MatchValue(value=p_id)),
            qm.FieldCondition(key="corpus_id", match=qm.MatchValue(value=c_id)),
            qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id)),
        ]
        version_must = [
            *base_must,
            qm.FieldCondition(key="doc_version", match=qm.MatchValue(value=version)),
        ]
        try:
            await run_in_threadpool(store.set_payload, payload={"is_active_version": False}, must=base_must)
            await run_in_threadpool(store.set_payload, payload={"is_active_version": True}, must=version_must)
        except Exception:
            pass

        return {
            "ok": True,
            "tenant_id": t_id,
            "project_id": p_id,
            "corpus_id": c_id,
            "doc_id": doc_id,
            "active_doc_version": str(row.active_doc_version),
        }

    @r.post("/runs/{run_id}/reassociate-scope")
    async def reassociate_run_scope(run_id: int, req: ReassociateRunScopeRequest) -> dict[str, Any]:
        """Re-associate a run (and its indexed chunks) to a new scope.

        Updates:
        - workflow_runs.tenant_id / project_id / meta.corpus_id
        - hitl_tasks scope columns for the run
        - active_doc_versions row for this doc scope (best-effort move)
        - Qdrant payload scope fields for matching chunks
        """
        target_tenant = clean_scope_id("tenant_id", req.tenant_id)
        target_project = clean_scope_id("project_id", req.project_id)
        target_corpus = clean_scope_id("corpus_id", req.corpus_id)

        with session_factory() as session:
            run = get_workflow_run(session, run_id=run_id)
            if run is None:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

            old_tenant = str(run.tenant_id)
            old_project = str(run.project_id)
            old_doc_id = str(run.doc_id)
            old_doc_version = str(run.doc_version)
            old_meta = dict(run.meta or {})
            old_corpus = str(old_meta.get("corpus_id") or settings.atlas_default_corpus_id)

            run.tenant_id = target_tenant
            run.project_id = target_project
            next_meta = dict(old_meta)
            next_meta["corpus_id"] = target_corpus
            run.meta = next_meta

            hitl_rows = list(
                session.query(HitlTaskRow)
                .filter(HitlTaskRow.run_id == run_id)
                .all()
            )
            for row in hitl_rows:
                row.tenant_id = target_tenant
                row.project_id = target_project

            # Best-effort: move ActiveDocVersion row to the new scope.
            old_adv = (
                session.query(ActiveDocVersion)
                .filter(
                    ActiveDocVersion.tenant_id == old_tenant,
                    ActiveDocVersion.project_id == old_project,
                    ActiveDocVersion.doc_id == old_doc_id,
                )
                .first()
            )
            moved_active_version = None
            if old_adv is not None:
                existing_new = (
                    session.query(ActiveDocVersion)
                    .filter(
                        ActiveDocVersion.tenant_id == target_tenant,
                        ActiveDocVersion.project_id == target_project,
                        ActiveDocVersion.doc_id == old_doc_id,
                    )
                    .first()
                )
                if existing_new is None:
                    existing_new = ActiveDocVersion(
                        tenant_id=target_tenant,
                        project_id=target_project,
                        doc_id=old_doc_id,
                        active_doc_version=old_adv.active_doc_version,
                    )
                    session.add(existing_new)
                moved_active_version = str(old_adv.active_doc_version)
                session.delete(old_adv)

            session.commit()

        # Update Qdrant payload scope for this doc+version in old scope.
        store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection=qdrant_collection())
        qdrant_updated = True
        qdrant_error = ""
        try:
            must = [
                qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=old_tenant)),
                qm.FieldCondition(key="project_id", match=qm.MatchValue(value=old_project)),
                qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=old_doc_id)),
                qm.FieldCondition(key="doc_version", match=qm.MatchValue(value=old_doc_version)),
            ]
            await run_in_threadpool(
                store.set_payload,
                payload={
                    "tenant_id": target_tenant,
                    "project_id": target_project,
                    "corpus_id": target_corpus,
                },
                must=must,
            )
        except Exception as exc:  # noqa: BLE001
            qdrant_updated = False
            qdrant_error = str(exc)

        return {
            "ok": True,
            "run_id": int(run_id),
            "doc_id": old_doc_id,
            "doc_version": old_doc_version,
            "from": {
                "tenant_id": old_tenant,
                "project_id": old_project,
                "corpus_id": old_corpus,
            },
            "to": {
                "tenant_id": target_tenant,
                "project_id": target_project,
                "corpus_id": target_corpus,
            },
            "hitl_rows_updated": len(hitl_rows),
            "active_doc_version_moved": moved_active_version,
            "qdrant_payload_updated": qdrant_updated,
            "qdrant_error": qdrant_error,
        }

    @r.post("/maintenance/cleanup-orphan-chunks")
    async def cleanup_orphan_chunks(req: CleanupOrphansRequest) -> dict[str, Any]:
        """Find and optionally delete Qdrant points with no matching WorkflowRun scope.

        A point is considered orphaned when (tenant_id, project_id, doc_id, doc_version)
        does not exist in workflow_runs.
        """
        max_points = max(1, min(int(req.max_points), 200000))

        with session_factory() as session:
            stmt = select(
                WorkflowRun.tenant_id,
                WorkflowRun.project_id,
                WorkflowRun.doc_id,
                WorkflowRun.doc_version,
            )
            if req.tenant_id:
                stmt = stmt.where(WorkflowRun.tenant_id == req.tenant_id)
            if req.project_id:
                stmt = stmt.where(WorkflowRun.project_id == req.project_id)
            run_rows = session.execute(stmt).all()

        valid_keys = {
            (str(t), str(p), str(d), str(v))
            for t, p, d, v in run_rows
        }

        collection = qdrant_collection()
        scanned = 0
        orphan_groups: dict[tuple[str, str, str, str], int] = {}

        scope_must_qm: list[qm.FieldCondition] = []
        if req.tenant_id:
            scope_must_qm.append(qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=req.tenant_id)))
        if req.project_id:
            scope_must_qm.append(qm.FieldCondition(key="project_id", match=qm.MatchValue(value=req.project_id)))
        if req.corpus_id:
            scope_must_qm.append(qm.FieldCondition(key="corpus_id", match=qm.MatchValue(value=req.corpus_id)))

        store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection=collection)
        points = await run_in_threadpool(
            store.scroll_points,
            must=scope_must_qm,
            limit=500,
            max_points=max_points,
        )
        scanned = len(points)

        for point in points:
            payload = {}
            if isinstance(point, dict):
                payload = point.get("payload") or {}
            else:
                payload = getattr(point, "payload", {}) or {}

            tenant_id = payload.get("tenant_id")
            project_id = payload.get("project_id")
            doc_id = payload.get("doc_id")
            doc_version = payload.get("doc_version")
            if not tenant_id or not project_id or not doc_id or not doc_version:
                continue

            key = (str(tenant_id), str(project_id), str(doc_id), str(doc_version))
            if key in valid_keys:
                continue
            orphan_groups[key] = orphan_groups.get(key, 0) + 1

        deleted_groups = 0
        if not req.dry_run and orphan_groups:
            for tenant_id, project_id, doc_id, doc_version in orphan_groups.keys():
                must = [
                    qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=tenant_id)),
                    qm.FieldCondition(key="project_id", match=qm.MatchValue(value=project_id)),
                    qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id)),
                    qm.FieldCondition(key="doc_version", match=qm.MatchValue(value=doc_version)),
                ]
                if req.corpus_id:
                    must.append(qm.FieldCondition(key="corpus_id", match=qm.MatchValue(value=req.corpus_id)))
                await run_in_threadpool(store.delete_by_filter, must=must)
                deleted_groups += 1

        orphan_points = sum(orphan_groups.values())

        # ── Reverse direction: DB runs with no Qdrant chunks (dangling runs) ──
        qdrant_keys: set[tuple[str, str, str, str]] = set()
        for point in points:
            payload = {}
            if isinstance(point, dict):
                payload = point.get("payload") or {}
            else:
                payload = getattr(point, "payload", {}) or {}
            t = payload.get("tenant_id")
            p = payload.get("project_id")
            d = payload.get("doc_id")
            v = payload.get("doc_version")
            if t and p and d and v:
                qdrant_keys.add((str(t), str(p), str(d), str(v)))

        dangling_runs_list: list[dict[str, Any]] = []
        with session_factory() as session:
            stmt = select(WorkflowRun)
            if req.tenant_id:
                stmt = stmt.where(WorkflowRun.tenant_id == req.tenant_id)
            if req.project_id:
                stmt = stmt.where(WorkflowRun.project_id == req.project_id)
            stmt = stmt.order_by(WorkflowRun.id.desc()).limit(200)
            all_runs = session.execute(stmt).scalars().all()
            for run in all_runs:
                rkey = (str(run.tenant_id), str(run.project_id), str(run.doc_id), str(run.doc_version))
                if rkey not in qdrant_keys:
                    dangling_runs_list.append({
                        "run_id": int(run.id),
                        "tenant_id": str(run.tenant_id),
                        "project_id": str(run.project_id),
                        "doc_id": str(run.doc_id),
                        "doc_version": str(run.doc_version),
                        "status": str(run.status),
                        "current_node": str(run.current_node),
                        "corpus_id": (run.meta or {}).get("corpus_id", ""),
                        "created_at": run.created_at.isoformat() if run.created_at else None,
                    })
                if len(dangling_runs_list) >= 50:
                    break

        return {
            "ok": True,
            "dry_run": bool(req.dry_run),
            "scanned_points": int(scanned),
            "max_points": int(max_points),
            "orphan_groups": int(len(orphan_groups)),
            "orphan_points_estimated": int(orphan_points),
            "deleted_groups": int(deleted_groups),
            "sample_orphans": [
                {
                    "tenant_id": k[0],
                    "project_id": k[1],
                    "doc_id": k[2],
                    "doc_version": k[3],
                    "points": v,
                }
                for k, v in list(orphan_groups.items())[:20]
            ],
            "dangling_runs": dangling_runs_list,
        }

    @r.post("/maintenance/adopt-orphan-group")
    async def adopt_orphan_group(req: AdoptOrphanGroupRequest) -> dict[str, Any]:
        """Adopt orphaned Qdrant chunks by moving them to a valid scope.

        Creates a synthetic WorkflowRun so the chunks are no longer orphaned,
        then updates the Qdrant payload scope fields.
        """
        target_tenant = clean_scope_id("tenant_id", req.tenant_id)
        target_project = clean_scope_id("project_id", req.project_id)
        target_corpus = clean_scope_id("corpus_id", req.corpus_id)
        old_tenant = req.old_tenant_id.strip()
        old_project = req.old_project_id.strip()
        old_doc_id = req.old_doc_id.strip()
        old_doc_version = req.old_doc_version.strip()

        if not old_tenant or not old_project or not old_doc_id or not old_doc_version:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="All old scope fields must be non-empty")

        # Count existing Qdrant points in the old scope.
        store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection=qdrant_collection())
        old_must = [
            qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=old_tenant)),
            qm.FieldCondition(key="project_id", match=qm.MatchValue(value=old_project)),
            qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=old_doc_id)),
            qm.FieldCondition(key="doc_version", match=qm.MatchValue(value=old_doc_version)),
        ]
        old_points = await run_in_threadpool(
            store.scroll_points, must=old_must, limit=1, max_points=1,
        )
        if not old_points:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=404,
                detail=f"No Qdrant points found for ({old_tenant}/{old_project}/{old_doc_id}/{old_doc_version})",
            )

        # Create a synthetic WorkflowRun so this group is no longer orphaned.
        with session_factory() as session:
            wf_run = create_workflow_run(
                session,
                req=WorkflowRunCreateRequest(
                    tenant_id=target_tenant,
                    project_id=target_project,
                    doc_id=old_doc_id,
                    doc_version=old_doc_version,
                    status="complete",
                    current_node="adopted",
                    meta={
                        "source": "orphan_adoption",
                        "corpus_id": target_corpus,
                        "adopted_from": {
                            "tenant_id": old_tenant,
                            "project_id": old_project,
                        },
                    },
                ),
            )
            session.commit()
            run_id = int(wf_run.id)

        # Update Qdrant payload scope.
        qdrant_updated = True
        qdrant_error = ""
        try:
            await run_in_threadpool(
                store.set_payload,
                payload={
                    "tenant_id": target_tenant,
                    "project_id": target_project,
                    "corpus_id": target_corpus,
                },
                must=old_must,
            )
        except Exception as exc:  # noqa: BLE001
            qdrant_updated = False
            qdrant_error = str(exc)

        return {
            "ok": True,
            "run_id": run_id,
            "doc_id": old_doc_id,
            "doc_version": old_doc_version,
            "from": {
                "tenant_id": old_tenant,
                "project_id": old_project,
            },
            "to": {
                "tenant_id": target_tenant,
                "project_id": target_project,
                "corpus_id": target_corpus,
            },
            "qdrant_payload_updated": qdrant_updated,
            "qdrant_error": qdrant_error,
        }

    @r.delete("/maintenance/dangling-run/{run_id}")
    async def delete_dangling_run(run_id: int) -> dict[str, Any]:
        """Delete a DB-only WorkflowRun that has NO matching Qdrant chunks.

        This is the inverse of orphan cleanup: the run exists in the DB but
        zero Qdrant points reference its (tenant, project, doc_id, doc_version).
        """
        with session_factory() as session:
            run = session.get(WorkflowRun, run_id)
            if run is None:
                from fastapi import HTTPException
                raise HTTPException(status_code=404, detail=f"WorkflowRun {run_id} not found")

            # Verify it really is dangling (no Qdrant points).
            store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection=qdrant_collection())
            must = [
                qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=str(run.tenant_id))),
                qm.FieldCondition(key="project_id", match=qm.MatchValue(value=str(run.project_id))),
                qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=str(run.doc_id))),
                qm.FieldCondition(key="doc_version", match=qm.MatchValue(value=str(run.doc_version))),
            ]
            pts = await run_in_threadpool(store.scroll_points, must=must, limit=1, max_points=1)
            if pts:
                from fastapi import HTTPException
                raise HTTPException(
                    status_code=409,
                    detail=f"Run {run_id} still has Qdrant chunks; not dangling",
                )

            doc_id = str(run.doc_id)
            session.delete(run)
            session.commit()

        return {"ok": True, "deleted_run_id": run_id, "doc_id": doc_id}

    @r.delete("/docs/{doc_id}")
    async def delete_doc(
        doc_id: str,
        tenant_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
        corpus_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """Delete all Qdrant chunks and active-version row for a document."""
        t_id = tenant_id or settings.atlas_default_tenant_id
        p_id = project_id or settings.atlas_default_project_id
        c_id = corpus_id or settings.atlas_default_corpus_id

        # Remove all Qdrant points for this doc in this scope.
        collection = qdrant_collection()
        delete_filter: dict[str, Any] = {
            "must": [
                {"key": "tenant_id", "match": {"value": t_id}},
                {"key": "project_id", "match": {"value": p_id}},
                {"key": "doc_id", "match": {"value": doc_id}},
            ]
        }
        res = await qdrant_post_json(settings, 
            f"/collections/{collection}/points/delete",
            {"filter": delete_filter, "wait": True},
        )
        points_deleted = (res.get("result") or {}).get("operation_id", 0)

        # Remove the active-doc-version row (best-effort).
        rows_deleted = 0
        try:
            from atlas.models import ActiveDocVersion

            with session_factory() as session:
                stmt = (
                    session.query(ActiveDocVersion)
                    .filter(
                        ActiveDocVersion.tenant_id == t_id,
                        ActiveDocVersion.project_id == p_id,
                        ActiveDocVersion.doc_id == doc_id,
                    )
                )
                rows = stmt.all()
                for row in rows:
                    session.delete(row)
                session.commit()
                rows_deleted = len(rows)
        except Exception:  # noqa: BLE001
            rows_deleted = 0

        return {
            "ok": True,
            "doc_id": doc_id,
            "tenant_id": t_id,
            "project_id": p_id,
            "corpus_id": c_id,
            "qdrant_operation_id": points_deleted,
            "active_version_rows_deleted": rows_deleted,
        }

    @r.get("/docs/{doc_id}/export")
    async def export_doc(
        doc_id: str,
        tenant_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
        corpus_id: str | None = Query(default=None),
        doc_version: str | None = Query(default=None),
        format: str = Query(default="full", description="Export format: 'full' (default) or 'lean' (markdown only)."),
    ) -> Any:
        from atlas.export_package import export_doc_lean, export_doc_package

        t_id = tenant_id or settings.atlas_default_tenant_id
        p_id = project_id or settings.atlas_default_project_id
        c_id = corpus_id or settings.atlas_default_corpus_id

        if (format or "full").lower() == "lean":
            blob = await export_doc_lean(
                session_factory=session_factory,
                tenant_id=t_id,
                project_id=p_id,
                corpus_id=c_id,
                doc_id=doc_id,
                doc_version=doc_version,
            )
            name_version = (doc_version or "active").replace("/", "_")
            filename = f"atlas_lean_{doc_id}_{name_version}.zip"
        else:
            blob = await export_doc_package(
                session_factory=session_factory,
                tenant_id=t_id,
                project_id=p_id,
                corpus_id=c_id,
                doc_id=doc_id,
                doc_version=doc_version,
            )
            name_version = (doc_version or "active").replace("/", "_")
            filename = f"atlas_export_{doc_id}_{name_version}.zip"

        return StreamingResponse(
            io.BytesIO(blob),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @r.get("/corpora/{corpus_id}/export")
    async def export_corpus(
        corpus_id: str,
        tenant_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
        max_docs: int = Query(default=200, ge=1, le=5000),
        format: str = Query(default="full", description="Export format: 'full' (default) or 'lean' (markdown only)."),
    ) -> Any:
        from atlas.corpus_package import export_corpus_lean, export_corpus_package

        t_id = tenant_id or settings.atlas_default_tenant_id
        p_id = project_id or settings.atlas_default_project_id
        c_id = (corpus_id or "").strip() or settings.atlas_default_corpus_id

        if (format or "full").lower() == "lean":
            blob = await export_corpus_lean(
                session_factory=session_factory,
                tenant_id=t_id,
                project_id=p_id,
                corpus_id=c_id,
                max_docs=int(max_docs),
            )
            filename = f"atlas_corpus_lean_{c_id}.zip"
        else:
            blob = await export_corpus_package(
                session_factory=session_factory,
                tenant_id=t_id,
                project_id=p_id,
                corpus_id=c_id,
                max_docs=int(max_docs),
            )
            filename = f"atlas_corpus_export_{c_id}.zip"

        return StreamingResponse(
            io.BytesIO(blob),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @r.get("/projects/{project_id}/export")
    async def export_project(
        project_id: str,
        tenant_id: str | None = Query(default=None),
        max_docs: int = Query(default=2000, ge=1, le=20000),
        format: str = Query(default="full", description="Export format: 'full' (default) or 'lean' (markdown only)."),
    ) -> Any:
        from atlas.corpus_package import export_project_lean, export_project_package

        t_id = tenant_id or settings.atlas_default_tenant_id
        p_id = (project_id or "").strip() or settings.atlas_default_project_id

        if (format or "full").lower() == "lean":
            blob = await export_project_lean(
                session_factory=session_factory,
                tenant_id=t_id,
                project_id=p_id,
                max_docs=int(max_docs),
            )
            filename = f"atlas_project_lean_{p_id}.zip"
        else:
            blob = await export_project_package(
                session_factory=session_factory,
                tenant_id=t_id,
                project_id=p_id,
                max_docs=int(max_docs),
            )
            filename = f"atlas_project_export_{p_id}.zip"

        return StreamingResponse(
            io.BytesIO(blob),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @r.get("/tenants/{tenant_id}/export")
    async def export_tenant(
        tenant_id: str,
        max_docs: int = Query(default=2000, ge=1, le=20000),
        format: str = Query(default="full", description="Export format: 'full' (default) or 'lean' (markdown only)."),
    ) -> Any:
        from atlas.corpus_package import export_tenant_lean, export_tenant_package

        t_id = (tenant_id or "").strip() or settings.atlas_default_tenant_id
        if (format or "full").lower() == "lean":
            blob = await export_tenant_lean(
                session_factory=session_factory,
                tenant_id=t_id,
                max_docs=int(max_docs),
            )
            filename = f"atlas_tenant_lean_{t_id}.zip"
        else:
            blob = await export_tenant_package(
                session_factory=session_factory,
                tenant_id=t_id,
                max_docs=int(max_docs),
            )
            filename = f"atlas_tenant_export_{t_id}.zip"

        return StreamingResponse(
            io.BytesIO(blob),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @r.get("/export")
    async def export_scoped(
        scope: str = Query(..., description="One of: document, corpus, project, tenant"),
        tenant_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
        corpus_id: str | None = Query(default=None),
        doc_id: str | None = Query(default=None),
        doc_version: str | None = Query(default=None),
        max_docs: int = Query(default=2000, ge=1, le=20000),
        format: str = Query(default="full", description="Export format: 'full' (default) or 'lean' (markdown only)."),
    ) -> Any:
        normalized_scope = (scope or "").strip().lower()
        t_id = tenant_id or settings.atlas_default_tenant_id

        if normalized_scope == "document":
            if not (doc_id or "").strip():
                from fastapi import HTTPException

                raise HTTPException(status_code=400, detail="doc_id is required for document scope")
            return await export_doc(
                doc_id=str(doc_id),
                tenant_id=t_id,
                project_id=project_id,
                corpus_id=corpus_id,
                doc_version=doc_version,
                format=format,
            )

        if normalized_scope == "corpus":
            if not (corpus_id or "").strip():
                from fastapi import HTTPException

                raise HTTPException(status_code=400, detail="corpus_id is required for corpus scope")
            return await export_corpus(
                corpus_id=str(corpus_id),
                tenant_id=t_id,
                project_id=project_id,
                max_docs=int(max_docs),
                format=format,
            )

        if normalized_scope == "project":
            p_id = (project_id or "").strip()
            if not p_id:
                from fastapi import HTTPException

                raise HTTPException(status_code=400, detail="project_id is required for project scope")
            return await export_project(
                project_id=p_id,
                tenant_id=t_id,
                max_docs=int(max_docs),
                format=format,
            )

        if normalized_scope == "tenant":
            return await export_tenant(
                tenant_id=t_id,
                max_docs=int(max_docs),
                format=format,
            )

        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="scope must be one of: document, corpus, project, tenant")

    @r.post("/corpora/{corpus_id}/import")
    async def import_corpus(
        corpus_id: str,
        file: UploadFile = File(...),
        tenant_id: str | None = Form(None),
        project_id: str | None = Form(None),
        is_finalized: bool = Form(True),
        is_sensitive: bool = Form(True),
    ) -> dict[str, Any]:
        from atlas.corpus_package import import_corpus_package

        t_id = tenant_id or settings.atlas_default_tenant_id
        p_id = project_id or settings.atlas_default_project_id
        c_id = (corpus_id or "").strip() or settings.atlas_default_corpus_id

        body = await file.read()
        return await import_corpus_package(
            config_manager=config_manager,
            session_factory=session_factory,
            tenant_id=t_id,
            project_id=p_id,
            corpus_id=c_id,
            zip_bytes=body,
            is_finalized=bool(is_finalized),
            is_sensitive=bool(is_sensitive),
        )

    return r
