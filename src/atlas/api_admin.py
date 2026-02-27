from __future__ import annotations

import io
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi import Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from qdrant_client.http import models as qm
from sqlalchemy.engine import Engine
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

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
from atlas.feedback_ledger import (
    FeedbackCreateRequest,
    FeedbackResponse,
    create_feedback,
    delete_feedback,
    feedback_category_counts,
    get_feedback,
    list_feedback,
    to_feedback_response,
)
from atlas.rule_suggester import suggest_cleanup_rule
from atlas.models import CleanupFeedback, Corpus, HitlTaskRow, NodeRun, Project, Tenant, WorkflowRun
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


class TenantCreateRequest(BaseModel):
    tenant_id: str
    display_name: str = ""
    description: str = ""


class ProjectCreateRequest(BaseModel):
    tenant_id: str
    project_id: str
    display_name: str = ""
    description: str = ""


class CorpusCreateRequest(BaseModel):
    tenant_id: str
    project_id: str
    corpus_id: str
    display_name: str = ""
    description: str = ""


class RuleSuggestionRequest(BaseModel):
    markdown_sample: str = ""
    issues: str = ""
    context: dict[str, str] = {}


class ApplyCleanupRuleRequest(BaseModel):
    """Push a cleanup rule into the active DB config version."""
    rule_yaml: str  # YAML string containing one rule entry
    name: str = ""  # Config-version name (optional)
    notes: str = "" # Config-version notes (optional)


class ImportCleanupRulesRequest(BaseModel):
    """Import cleanup rules from a YAML string."""
    rules_yaml: str  # YAML string containing a list of rule entries
    mode: str = "replace"  # 'replace' (overwrite all) or 'merge' (add/update by name)
    name: str = ""  # Config-version name (optional)
    notes: str = ""  # Config-version notes (optional)


class CleanupDryRunRequest(BaseModel):
    """Test cleanup rules against a markdown sample without ingesting."""
    markdown_sample: str
    tenant_id: str = "local"
    project_id: str = "default"
    corpus_id: str = "default"
    mime_type: str = "application/pdf"
    filename: str = ""


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

    def _group_count(
        session: Session,
        *,
        label_col: Any,
        from_table: Any,
        where: Any | None = None,
    ) -> dict[str, int]:
        stmt = select(label_col, func.count()).select_from(from_table)
        if where is not None:
            stmt = stmt.where(where)
        stmt = stmt.group_by(label_col)
        res = session.execute(stmt).all()
        out: dict[str, int] = {}
        for k, v in res:
            if k is None:
                continue
            out[str(k)] = int(v)
        return out

    def _ledger_summary(session: Session) -> dict[str, Any]:
        import datetime as _dt

        # Compute 24h cutoff once as a timezone-aware datetime so all sub-queries use a
        # consistent value and comparisons work correctly with DateTime(timezone=True) columns.
        cutoff_24h: _dt.datetime = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=24)

        run_status = _group_count(session, label_col=WorkflowRun.status, from_table=WorkflowRun)
        run_node = _group_count(session, label_col=WorkflowRun.current_node, from_table=WorkflowRun)
        run_failed_codes = _group_count(
            session,
            label_col=WorkflowRun.error_code,
            from_table=WorkflowRun,
            where=(WorkflowRun.error_code != ""),
        )

        node_status = _group_count(session, label_col=NodeRun.status, from_table=NodeRun)
        node_name = _group_count(session, label_col=NodeRun.node_name, from_table=NodeRun)
        node_failed_codes = _group_count(
            session,
            label_col=NodeRun.error_code,
            from_table=NodeRun,
            where=(NodeRun.error_code != ""),
        )

        hitl_status = _group_count(session, label_col=HitlTaskRow.status, from_table=HitlTaskRow)
        hitl_unassigned_pending = session.execute(
            select(func.count())
            .select_from(HitlTaskRow)
            .where(HitlTaskRow.status == "pending")
            .where(HitlTaskRow.assigned_to == "")
        ).scalar_one()

        run_total = session.execute(select(func.count()).select_from(WorkflowRun)).scalar_one()
        node_total = session.execute(select(func.count()).select_from(NodeRun)).scalar_one()
        hitl_total = session.execute(select(func.count()).select_from(HitlTaskRow)).scalar_one()

        # Unique docs (distinct doc_id values across all runs)
        docs_unique = session.execute(
            select(func.count(func.distinct(WorkflowRun.doc_id))).select_from(WorkflowRun)
        ).scalar_one()

        # Runs created in the last 24 hours (uses cutoff_24h computed at function start)
        runs_last_24h = session.execute(
            select(func.count())
            .select_from(WorkflowRun)
            .where(WorkflowRun.created_at >= cutoff_24h)
        ).scalar_one()

        return {
            "workflow_runs": {
                "total": int(run_total),
                "docs_unique": int(docs_unique),
                "runs_last_24h": int(runs_last_24h),
                "by_status": run_status,
                "by_current_node": run_node,
                "failed_by_error_code": run_failed_codes,
            },
            "node_runs": {
                "total": int(node_total),
                "by_status": node_status,
                "by_node_name": node_name,
                "failed_by_error_code": node_failed_codes,
            },
            "hitl_tasks": {
                "total": int(hitl_total),
                "by_status": hitl_status,
                "pending_unassigned": int(hitl_unassigned_pending),
            },
        }

    def _qdrant_url() -> str:
        return settings.atlas_qdrant_url.rstrip("/")

    def _qdrant_collection() -> str:
        # Keep consistent with current RAG MVP behavior.
        return "atlas_chunks"

    async def _qdrant_get_json(path: str) -> dict[str, Any]:
        import httpx

        url = f"{_qdrant_url()}{path}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()

    async def _qdrant_post_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        import httpx

        url = f"{_qdrant_url()}{path}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def _qdrant_clear_collection_points(*, collection: str) -> dict[str, Any]:
        import httpx

        try:
            # Delete all points but keep the collection (no vector-size knowledge required).
            return await _qdrant_post_json(
                f"/collections/{collection}/points/delete",
                {"filter": {}, "wait": True},
            )
        except httpx.HTTPStatusError as e:
            # If the collection doesn't exist, treat as already cleared.
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
            collection = _qdrant_collection()
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

    def _parse_cursor(cursor: str | None) -> str | int | None:
        if cursor is None or cursor == "":
            return None
        if cursor.isdigit():
            return int(cursor)
        return cursor

    def _clean_scope_id(label: str, value: str) -> str:
        v = (value or "").strip()
        if not v:
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail=f"{label} must be non-empty")
        return v

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

    @r.get("/tenants")
    def list_tenants(active_only: bool = Query(default=True)) -> dict[str, Any]:
        with session_factory() as session:
            stmt = select(Tenant).order_by(Tenant.tenant_id.asc())
            if active_only:
                stmt = stmt.where(Tenant.is_active.is_(True))
            rows = list(session.execute(stmt).scalars().all())
        return {
            "tenants": [
                {
                    "tenant_id": t.tenant_id,
                    "display_name": t.display_name,
                    "description": t.description,
                    "is_active": bool(t.is_active),
                }
                for t in rows
            ]
        }

    @r.post("/tenants")
    def create_tenant(req: TenantCreateRequest) -> dict[str, Any]:
        t_id = _clean_scope_id("tenant_id", req.tenant_id)
        row = Tenant(
            tenant_id=t_id,
            display_name=(req.display_name or "").strip(),
            description=(req.description or "").strip(),
            is_active=True,
        )
        with session_factory() as session:
            try:
                session.add(row)
                session.commit()
            except IntegrityError as e:
                session.rollback()
                from fastapi import HTTPException

                raise HTTPException(status_code=409, detail="tenant already exists") from e
        return {"ok": True, "tenant_id": t_id}

    @r.delete("/tenants/{tenant_id}")
    def delete_tenant(tenant_id: str) -> dict[str, Any]:
        t_id = _clean_scope_id("tenant_id", tenant_id)
        with session_factory() as session:
            proj_count = int(
                session.execute(
                    select(func.count()).select_from(Project).where(Project.tenant_id == t_id)
                ).scalar_one()
            )
            corp_count = int(
                session.execute(
                    select(func.count()).select_from(Corpus).where(Corpus.tenant_id == t_id)
                ).scalar_one()
            )
            if proj_count > 0 or corp_count > 0:
                from fastapi import HTTPException

                raise HTTPException(status_code=409, detail="tenant has projects/corpora; delete children first")

            row = session.execute(select(Tenant).where(Tenant.tenant_id == t_id)).scalars().first()
            if row is None:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="tenant not found")
            session.delete(row)
            session.commit()
        return {"ok": True, "tenant_id": t_id}

    @r.get("/projects")
    def list_projects(tenant_id: str | None = Query(default=None), active_only: bool = Query(default=True)) -> dict[str, Any]:
        with session_factory() as session:
            stmt = select(Project).order_by(Project.tenant_id.asc(), Project.project_id.asc())
            if tenant_id:
                stmt = stmt.where(Project.tenant_id == tenant_id)
            if active_only:
                stmt = stmt.where(Project.is_active.is_(True))
            rows = list(session.execute(stmt).scalars().all())
        return {
            "projects": [
                {
                    "tenant_id": p.tenant_id,
                    "project_id": p.project_id,
                    "display_name": p.display_name,
                    "description": p.description,
                    "is_active": bool(p.is_active),
                }
                for p in rows
            ]
        }

    @r.post("/projects")
    def create_project(req: ProjectCreateRequest) -> dict[str, Any]:
        t_id = _clean_scope_id("tenant_id", req.tenant_id)
        p_id = _clean_scope_id("project_id", req.project_id)
        row = Project(
            tenant_id=t_id,
            project_id=p_id,
            display_name=(req.display_name or "").strip(),
            description=(req.description or "").strip(),
            is_active=True,
        )
        with session_factory() as session:
            tenant = session.execute(select(Tenant).where(Tenant.tenant_id == t_id)).scalars().first()
            if tenant is None:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="tenant not found")
            try:
                session.add(row)
                session.commit()
            except IntegrityError as e:
                session.rollback()
                from fastapi import HTTPException

                raise HTTPException(status_code=409, detail="project already exists in tenant") from e
        return {"ok": True, "tenant_id": t_id, "project_id": p_id}

    @r.delete("/projects/{project_id}")
    def delete_project(project_id: str, tenant_id: str = Query(...)) -> dict[str, Any]:
        t_id = _clean_scope_id("tenant_id", tenant_id)
        p_id = _clean_scope_id("project_id", project_id)
        with session_factory() as session:
            corp_count = int(
                session.execute(
                    select(func.count())
                    .select_from(Corpus)
                    .where(Corpus.tenant_id == t_id, Corpus.project_id == p_id)
                ).scalar_one()
            )
            if corp_count > 0:
                from fastapi import HTTPException

                raise HTTPException(status_code=409, detail="project has corpora; delete children first")

            row = session.execute(
                select(Project).where(Project.tenant_id == t_id, Project.project_id == p_id)
            ).scalars().first()
            if row is None:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="project not found")
            session.delete(row)
            session.commit()
        return {"ok": True, "tenant_id": t_id, "project_id": p_id}

    @r.get("/corpora")
    def list_corpora(
        tenant_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
        active_only: bool = Query(default=True),
    ) -> dict[str, Any]:
        with session_factory() as session:
            stmt = select(Corpus).order_by(Corpus.tenant_id.asc(), Corpus.project_id.asc(), Corpus.corpus_id.asc())
            if tenant_id:
                stmt = stmt.where(Corpus.tenant_id == tenant_id)
            if project_id:
                stmt = stmt.where(Corpus.project_id == project_id)
            if active_only:
                stmt = stmt.where(Corpus.is_active.is_(True))
            rows = list(session.execute(stmt).scalars().all())
        return {
            "corpora": [
                {
                    "tenant_id": c.tenant_id,
                    "project_id": c.project_id,
                    "corpus_id": c.corpus_id,
                    "display_name": c.display_name,
                    "description": c.description,
                    "is_active": bool(c.is_active),
                }
                for c in rows
            ]
        }

    @r.post("/corpora")
    def create_corpus(req: CorpusCreateRequest) -> dict[str, Any]:
        t_id = _clean_scope_id("tenant_id", req.tenant_id)
        p_id = _clean_scope_id("project_id", req.project_id)
        c_id = _clean_scope_id("corpus_id", req.corpus_id)
        row = Corpus(
            tenant_id=t_id,
            project_id=p_id,
            corpus_id=c_id,
            display_name=(req.display_name or "").strip(),
            description=(req.description or "").strip(),
            is_active=True,
        )
        with session_factory() as session:
            project = session.execute(
                select(Project).where(Project.tenant_id == t_id, Project.project_id == p_id)
            ).scalars().first()
            if project is None:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="project not found")
            try:
                session.add(row)
                session.commit()
            except IntegrityError as e:
                session.rollback()
                from fastapi import HTTPException

                raise HTTPException(status_code=409, detail="corpus already exists in project") from e
        return {"ok": True, "tenant_id": t_id, "project_id": p_id, "corpus_id": c_id}

    @r.delete("/corpora/{corpus_id}")
    def delete_corpus(corpus_id: str, tenant_id: str = Query(...), project_id: str = Query(...)) -> dict[str, Any]:
        t_id = _clean_scope_id("tenant_id", tenant_id)
        p_id = _clean_scope_id("project_id", project_id)
        c_id = _clean_scope_id("corpus_id", corpus_id)
        with session_factory() as session:
            row = session.execute(
                select(Corpus).where(
                    Corpus.tenant_id == t_id,
                    Corpus.project_id == p_id,
                    Corpus.corpus_id == c_id,
                )
            ).scalars().first()
            if row is None:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="corpus not found")
            session.delete(row)
            session.commit()
        return {"ok": True, "tenant_id": t_id, "project_id": p_id, "corpus_id": c_id}

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

    # ------------------------------------------------------------------
    # Cleanup feedback endpoints (Phase 7B)
    # ------------------------------------------------------------------

    @r.post("/cleanup-feedback", response_model=FeedbackResponse, status_code=201)
    def feedback_create(req: FeedbackCreateRequest) -> FeedbackResponse:
        with session_factory() as session:
            row = create_feedback(session, req=req)
        return to_feedback_response(row)

    @r.get("/cleanup-feedback", response_model=list[FeedbackResponse])
    def feedback_list(
        tenant_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
        corpus_id: str | None = Query(default=None),
        doc_id: str | None = Query(default=None),
        category: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> list[FeedbackResponse]:
        with session_factory() as session:
            rows = list_feedback(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                corpus_id=corpus_id,
                doc_id=doc_id,
                category=category,
                limit=int(limit),
            )
        return [to_feedback_response(r) for r in rows]

    @r.get("/cleanup-feedback/categories")
    def feedback_categories(
        tenant_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
        corpus_id: str | None = Query(default=None),
    ) -> dict[str, int]:
        with session_factory() as session:
            return feedback_category_counts(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                corpus_id=corpus_id,
            )

    @r.get("/cleanup-feedback/{feedback_id}", response_model=FeedbackResponse)
    def feedback_get(feedback_id: int) -> FeedbackResponse:
        with session_factory() as session:
            row = get_feedback(session, feedback_id=feedback_id)
        if row is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="feedback not found")
        return to_feedback_response(row)

    @r.delete("/cleanup-feedback/{feedback_id}")
    def feedback_delete(feedback_id: int) -> dict[str, bool]:
        with session_factory() as session:
            deleted = delete_feedback(session, feedback_id=feedback_id)
        if not deleted:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="feedback not found")
        return {"deleted": True}

    # ------------------------------------------------------------------
    # Cleanup rule suggestion endpoint (Phase 7D)
    # ------------------------------------------------------------------

    @r.post("/cleanup-rules/suggest")
    async def cleanup_rule_suggest(req: RuleSuggestionRequest) -> dict[str, Any]:
        """Ask the LLM to suggest a cleanup rule for the given markdown sample."""
        from atlas.llm.registry import ModelRegistry

        eff = config_manager.get()
        registry = ModelRegistry(settings=settings, models_cfg=eff.models)
        # Prefer a dedicated chat_model role; fall back to refine_model.
        for role in ("chat_model", "refine_model"):
            try:
                resolved = registry.resolve(role)
                break
            except KeyError:
                continue
        else:
            from fastapi import HTTPException
            raise HTTPException(status_code=503, detail="No chat or refine model configured")
        provider = registry.provider_for(resolved.provider_name)
        result = await suggest_cleanup_rule(
            provider=provider,
            model=resolved.model_name,
            markdown_sample=req.markdown_sample,
            issues=req.issues,
            context=req.context,
            params=resolved.params,
        )
        return result

    @r.post("/cleanup-rules/apply")
    def apply_cleanup_rule(req: ApplyCleanupRuleRequest) -> dict[str, Any]:
        """Validate and apply a cleanup rule by creating a new DB config version.

        The rule YAML is parsed, validated, and appended to the effective
        ``cleanup_rules`` list.  A new config version is created and activated
        so the pipeline picks it up without a container restart.
        """
        import yaml as _yaml
        from fastapi import HTTPException
        from atlas.startup_validation import validate_cleanup_rules

        # 1. Parse the YAML
        try:
            parsed = _yaml.safe_load(req.rule_yaml)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid YAML: {exc}")

        # Normalize: single dict → list
        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list) or not parsed:
            raise HTTPException(status_code=400, detail="Expected a YAML list of rule entries")

        # 2. Validate the rule(s) against the schema
        errors = validate_cleanup_rules(parsed)
        if errors:
            raise HTTPException(status_code=422, detail={"validation_errors": errors})

        # 3. Get the current effective cleanup_rules list
        yaml_defaults = config_manager.get()
        with session_factory() as session:
            active = get_active_config_version(session)

        if active is not None:
            current_pipeline = active.payload.get("pipeline", {})
        else:
            current_pipeline = yaml_defaults.pipeline

        existing_rules: list[dict[str, Any]] = list(current_pipeline.get("cleanup_rules", []) or [])

        # Deduplicate by name — replace existing rule with same name, append otherwise
        new_names = {r["name"] for r in parsed if "name" in r}
        merged_rules = [r for r in existing_rules if r.get("name") not in new_names]
        merged_rules.extend(parsed)

        # 4. Create a new config version with the patched cleanup_rules
        from atlas.config_versions import ConfigVersionCreateRequest, create_config_version

        cv_req = ConfigVersionCreateRequest(
            name=req.name or f"apply-rule-{parsed[0].get('name', 'unknown')}",
            notes=req.notes or f"Applied cleanup rule(s): {', '.join(new_names)}",
            base="current",
            patch={"pipeline": {"cleanup_rules": merged_rules}},
            activate=True,
        )
        row = create_config_version(session_factory(), req=cv_req, yaml_defaults=yaml_defaults)

        return {
            "ok": True,
            "config_version_id": row.id,
            "config_hash": row.config_hash,
            "rules_count": len(merged_rules),
            "applied": [r.get("name") for r in parsed],
        }

    @r.post("/cleanup-rules/dry-run")
    async def cleanup_rules_dry_run(req: CleanupDryRunRequest) -> dict[str, Any]:
        """Test the active cleanup rules against a markdown sample.

        Returns the cleaned markdown, the matched rule name, per-step diffs,
        and the doc-context used for matching — useful for diagnosing why a
        rule does or doesn't fire.
        """
        from atlas.pipeline.cleanup import CleanupNode
        from atlas.pipeline.cleanup_rules import DocContext, find_matching_rule, parse_rules

        # Load effective config
        yaml_defaults = config_manager.get()
        with session_factory() as session:
            active = get_active_config_version(session)

        if active is not None:
            pipeline_cfg = active.payload.get("pipeline", {})
            source = f"db:config_version#{active.id}"
        else:
            pipeline_cfg = yaml_defaults.pipeline
            source = "yaml-defaults"

        raw_rules = list((pipeline_cfg.get("cleanup_rules", []) or []))
        parsed_rules = parse_rules(raw_rules)

        doc_ctx = DocContext(
            tenant_id=req.tenant_id,
            project_id=req.project_id,
            corpus_id=req.corpus_id,
            mime_type=req.mime_type,
            filename=req.filename,
        )

        matched = find_matching_rule(parsed_rules, doc_ctx)

        # Run the full cleanup node to get the result
        node = CleanupNode()
        result = await node.clean(
            markdown=req.markdown_sample,
            doc_context={
                "tenant_id": req.tenant_id,
                "project_id": req.project_id,
                "corpus_id": req.corpus_id,
                "mime_type": req.mime_type,
                "filename": req.filename,
            },
            config=pipeline_cfg,
        )

        return {
            "config_source": source,
            "rules_available": len(parsed_rules),
            "rules_names": [r.name for r in parsed_rules],
            "doc_context": {
                "tenant_id": req.tenant_id,
                "project_id": req.project_id,
                "corpus_id": req.corpus_id,
                "mime_type": req.mime_type,
                "filename": req.filename,
            },
            "matched_rule": matched.name if matched else None,
            "matched_rule_steps": len(matched.steps) if matched else 0,
            "rules_applied": result.rules_applied,
            "rule_tags": result.rule_tags,
            "fix_counts": result.fix_counts,
            "input_length": len(req.markdown_sample),
            "output_length": len(result.cleaned_markdown),
            "changed": req.markdown_sample != result.cleaned_markdown,
            "cleaned_markdown": result.cleaned_markdown,
        }

    @r.get("/cleanup-rules/export")
    def export_cleanup_rules() -> StreamingResponse:
        """Export the active cleanup rules as a downloadable YAML file."""
        import yaml as _yaml

        yaml_defaults = config_manager.get()
        with session_factory() as session:
            active = get_active_config_version(session)

        if active is not None:
            current_pipeline = active.payload.get("pipeline", {})
        else:
            current_pipeline = yaml_defaults.pipeline

        rules: list[dict[str, Any]] = list(current_pipeline.get("cleanup_rules", []) or [])
        yaml_str = _yaml.dump(rules, default_flow_style=False, sort_keys=False, allow_unicode=True)
        buf = io.BytesIO(yaml_str.encode("utf-8"))
        return StreamingResponse(
            buf,
            media_type="application/x-yaml",
            headers={"Content-Disposition": "attachment; filename=cleanup_rules.yaml"},
        )

    @r.post("/cleanup-rules/import")
    def import_cleanup_rules(req: ImportCleanupRulesRequest) -> dict[str, Any]:
        """Import cleanup rules from YAML.

        Modes:
        - ``replace`` (default): overwrites the entire cleanup_rules list.
        - ``merge``: adds new rules by name, replaces existing rules with the same name.
        """
        import yaml as _yaml
        from fastapi import HTTPException
        from atlas.startup_validation import validate_cleanup_rules

        if req.mode not in ("replace", "merge"):
            raise HTTPException(status_code=400, detail="mode must be 'replace' or 'merge'")

        # 1. Parse
        try:
            parsed = _yaml.safe_load(req.rules_yaml)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid YAML: {exc}")

        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list):
            raise HTTPException(status_code=400, detail="Expected a YAML list of rule entries")

        # 2. Validate
        errors = validate_cleanup_rules(parsed)
        if errors:
            raise HTTPException(status_code=422, detail={"validation_errors": errors})

        # 3. Build final rule list
        yaml_defaults = config_manager.get()
        with session_factory() as session:
            active = get_active_config_version(session)

        if req.mode == "merge":
            if active is not None:
                current_pipeline = active.payload.get("pipeline", {})
            else:
                current_pipeline = yaml_defaults.pipeline
            existing: list[dict[str, Any]] = list(current_pipeline.get("cleanup_rules", []) or [])
            new_names = {r["name"] for r in parsed if "name" in r}
            merged = [r for r in existing if r.get("name") not in new_names]
            merged.extend(parsed)
            final_rules = merged
        else:
            final_rules = parsed

        imported_names = [r.get("name", "unnamed") for r in parsed]

        # 4. Create config version
        cv_req = ConfigVersionCreateRequest(
            name=req.name or f"import-rules-{req.mode}",
            notes=req.notes or f"Imported {len(parsed)} rule(s) ({req.mode}): {', '.join(imported_names)}",
            base="current",
            patch={"pipeline": {"cleanup_rules": final_rules}},
            activate=True,
        )
        row = create_config_version(session_factory(), req=cv_req, yaml_defaults=yaml_defaults)

        return {
            "ok": True,
            "mode": req.mode,
            "config_version_id": row.id,
            "config_hash": row.config_hash,
            "rules_count": len(final_rules),
            "imported": imported_names,
        }

    @r.delete("/cleanup-rules/{rule_name}")
    def remove_cleanup_rule(rule_name: str) -> dict[str, Any]:
        """Remove a cleanup rule by name.

        Creates a new config version with the rule removed from the list.
        """
        from fastapi import HTTPException
        from atlas.config_versions import ConfigVersionCreateRequest, create_config_version

        yaml_defaults = config_manager.get()
        with session_factory() as session:
            active = get_active_config_version(session)

        if active is not None:
            current_pipeline = active.payload.get("pipeline", {})
        else:
            current_pipeline = yaml_defaults.pipeline

        existing_rules: list[dict[str, Any]] = list(current_pipeline.get("cleanup_rules", []) or [])
        filtered = [r for r in existing_rules if r.get("name") != rule_name]

        if len(filtered) == len(existing_rules):
            raise HTTPException(status_code=404, detail=f"No rule named '{rule_name}' found")

        cv_req = ConfigVersionCreateRequest(
            name=f"remove-rule-{rule_name}",
            notes=f"Removed cleanup rule: {rule_name}",
            base="current",
            patch={"pipeline": {"cleanup_rules": filtered}},
            activate=True,
        )
        row = create_config_version(session_factory(), req=cv_req, yaml_defaults=yaml_defaults)

        return {
            "ok": True,
            "config_version_id": row.id,
            "config_hash": row.config_hash,
            "rules_count": len(filtered),
            "removed": rule_name,
        }

    @r.post("/self-test")
    def self_test(request: Request, timeout_s: float = Query(default=20.0, ge=1.0, le=120.0)) -> dict[str, Any]:
        api_url = str(request.base_url).rstrip("/")
        incoming_token = request.headers.get("X-Atlas-Admin-Token")

        summary = run_scenarios(
            api_url=api_url,
            qdrant_url=settings.atlas_qdrant_url,
            collection=_qdrant_collection(),
            timeout_s=float(timeout_s),
            admin_token=incoming_token or settings.atlas_admin_token or None,
        )
        return {
            "ok": bool(summary.ok),
            "results": [{"name": r.name, "ok": bool(r.ok), "detail": r.detail} for r in summary.results],
        }

    @r.get("/looking-glass/qdrant")
    async def looking_glass_qdrant() -> dict[str, Any]:
        collection = _qdrant_collection()
        info = await _qdrant_get_json(f"/collections/{collection}")
        # best-effort: count points
        count = await _qdrant_post_json(
            f"/collections/{collection}/points/count",
            {"exact": True, "filter": {}},
        )
        return {
            "collection": collection,
            "collection_info": info.get("result"),
            "points_count": (count.get("result") or {}).get("count"),
        }

    @r.get("/looking-glass/ledger/summary")
    def looking_glass_ledger_summary() -> dict[str, Any]:
        with session_factory() as session:
            return _ledger_summary(session)

    @r.get("/looking-glass/ledger/in-flight", response_model=list[WorkflowRunResponse])
    def looking_glass_in_flight(
        limit: int = Query(default=50, ge=1, le=500),
        tenant_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
    ) -> list[WorkflowRunResponse]:
        with session_factory() as session:
            stmt = (
                select(WorkflowRun)
                .where(WorkflowRun.status.not_in(["completed", "failed"]))
                .order_by(WorkflowRun.updated_at.desc(), WorkflowRun.id.desc())
                .limit(int(limit))
            )
            if tenant_id:
                stmt = stmt.where(WorkflowRun.tenant_id == tenant_id)
            if project_id:
                stmt = stmt.where(WorkflowRun.project_id == project_id)
            rows = list(session.execute(stmt).scalars().all())
            return [to_run_response(r) for r in rows]

    @r.get("/looking-glass/ledger/failures")
    def looking_glass_failures(
        limit: int = Query(default=50, ge=1, le=500),
        tenant_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        with session_factory() as session:
            run_stmt = (
                select(WorkflowRun)
                .where(WorkflowRun.status == "failed")
                .order_by(WorkflowRun.updated_at.desc(), WorkflowRun.id.desc())
                .limit(int(limit))
            )
            if tenant_id:
                run_stmt = run_stmt.where(WorkflowRun.tenant_id == tenant_id)
            if project_id:
                run_stmt = run_stmt.where(WorkflowRun.project_id == project_id)
            runs = list(session.execute(run_stmt).scalars().all())

            failures: list[dict[str, Any]] = []
            for run in runs:
                node_stmt = (
                    select(NodeRun)
                    .where(NodeRun.run_id == run.id)
                    .where((NodeRun.status == "failed") | (NodeRun.error_code != "") | (NodeRun.error_message != ""))
                    .order_by(NodeRun.id.desc())
                    .limit(25)
                )
                nodes = list(session.execute(node_stmt).scalars().all())
                failures.append(
                    {
                        "run": to_run_response(run).model_dump(),
                        "node_errors": [to_node_run_response(n).model_dump() for n in nodes],
                    }
                )

            return {"failures": failures}

    @r.get("/looking-glass/ledger/hitl", response_model=list[HitlTaskResponse])
    def looking_glass_hitl(
        status: str | None = Query(default="pending"),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> list[HitlTaskResponse]:
        with session_factory() as session:
            rows = list_hitl_tasks(session, status=status or None, limit=int(limit))
            return [to_hitl_response(r) for r in rows]

    @r.get("/looking-glass/inventory")
    async def looking_glass_inventory(
        max_points: int = Query(default=5000, ge=1, le=200000),
        page_size: int = Query(default=500, ge=50, le=2000),
    ) -> dict[str, Any]:
        with session_factory() as session:
            ledger = _ledger_summary(session)

        collection = _qdrant_collection()
        offset: str | int | None = None
        scanned = 0
        truncated = False

        unique_docs: set[str] = set()
        unique_tenants: set[str] = set()
        unique_projects: set[str] = set()

        points_by_tenant: dict[str, int] = {}
        points_by_project: dict[str, int] = {}
        points_by_tenant_project: dict[str, int] = {}
        points_finalized: int = 0
        points_nonfinalized: int = 0

        while scanned < max_points:
            body: dict[str, Any] = {
                "limit": int(min(page_size, max_points - scanned)),
                "with_payload": True,
                "with_vector": False,
            }
            if offset is not None:
                body["offset"] = offset

            res = await _qdrant_post_json(f"/collections/{collection}/points/scroll", body)
            result = res.get("result") or {}
            points = result.get("points") or []
            offset = result.get("next_page_offset")

            if not points:
                break

            for p in points:
                scanned += 1
                payload = p.get("payload") or {}
                doc_id = payload.get("doc_id")
                tenant_id = payload.get("tenant_id")
                project_id = payload.get("project_id")
                is_finalized = payload.get("is_finalized")

                if doc_id:
                    unique_docs.add(str(doc_id))
                if tenant_id:
                    unique_tenants.add(str(tenant_id))
                    points_by_tenant[str(tenant_id)] = points_by_tenant.get(str(tenant_id), 0) + 1
                if project_id:
                    unique_projects.add(str(project_id))
                    points_by_project[str(project_id)] = points_by_project.get(str(project_id), 0) + 1

                if tenant_id and project_id:
                    key = f"{tenant_id}/{project_id}"
                    points_by_tenant_project[key] = points_by_tenant_project.get(key, 0) + 1

                if is_finalized is True:
                    points_finalized += 1
                elif is_finalized is False:
                    points_nonfinalized += 1

                if scanned >= max_points:
                    break

            if scanned >= max_points and offset is not None:
                truncated = True
                break
            if offset is None:
                break

        return {
            "collection": collection,
            "ledger": ledger,
            "scanned_points": scanned,
            "truncated": truncated,
            "unique_docs": len(unique_docs),
            "unique_tenants": len(unique_tenants),
            "unique_projects": len(unique_projects),
            "points_finalized": points_finalized,
            "points_nonfinalized": points_nonfinalized,
            "points_by_tenant": points_by_tenant,
            "points_by_project": points_by_project,
            "points_by_tenant_project": points_by_tenant_project,
        }

    @r.get("/looking-glass/docs")
    async def looking_glass_docs(
        limit: int = Query(default=50, ge=1, le=200),
        cursor: str | None = Query(default=None),
        scan_page_size: int = Query(default=200, ge=50, le=1000),
        tenant_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
        corpus_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        collection = _qdrant_collection()
        next_offset = _parse_cursor(cursor)

        docs: dict[str, dict[str, Any]] = {}
        scanned_pages = 0

        # Build optional Qdrant filter from scope params.
        scope_must: list[dict[str, Any]] = []
        if tenant_id:
            scope_must.append({"key": "tenant_id", "match": {"value": tenant_id}})
        if project_id:
            scope_must.append({"key": "project_id", "match": {"value": project_id}})
        if corpus_id:
            scope_must.append({"key": "corpus_id", "match": {"value": corpus_id}})

        while len(docs) < limit and scanned_pages < 10:
            scanned_pages += 1
            body: dict[str, Any] = {
                "limit": int(scan_page_size),
                "with_payload": True,
                "with_vector": False,
            }
            if next_offset is not None:
                body["offset"] = next_offset
            if scope_must:
                body["filter"] = {"must": scope_must}

            res = await _qdrant_post_json(f"/collections/{collection}/points/scroll", body)
            result = res.get("result") or {}
            points = result.get("points") or []
            next_offset = result.get("next_page_offset")

            for p in points:
                payload = p.get("payload") or {}
                doc_id = payload.get("doc_id")
                if not doc_id or doc_id in docs:
                    continue
                docs[str(doc_id)] = {
                    "doc_id": str(doc_id),
                    "tenant_id": payload.get("tenant_id"),
                    "project_id": payload.get("project_id"),
                    "corpus_id": payload.get("corpus_id"),
                    "doc_version": payload.get("doc_version"),
                    "source_mime_type": payload.get("source_mime_type"),
                    "is_finalized": payload.get("is_finalized"),
                    "is_sensitive": payload.get("is_sensitive"),
                    "created_at": payload.get("created_at"),
                }
                if len(docs) >= limit:
                    break

            if not points or next_offset is None:
                break

        return {
            "collection": collection,
            "docs": list(docs.values()),
            "next_cursor": None if next_offset is None else str(next_offset),
        }

    @r.get("/looking-glass/docs/{doc_id}")
    async def looking_glass_doc_detail(
        doc_id: str,
        limit: int = Query(default=100, ge=1, le=500),
        cursor: str | None = Query(default=None),
    ) -> dict[str, Any]:
        collection = _qdrant_collection()
        next_offset = _parse_cursor(cursor)

        body: dict[str, Any] = {
            "limit": int(limit),
            "with_payload": True,
            "with_vector": False,
            "filter": {"must": [{"key": "doc_id", "match": {"value": doc_id}}]},
        }
        if next_offset is not None:
            body["offset"] = next_offset

        res = await _qdrant_post_json(f"/collections/{collection}/points/scroll", body)
        result = res.get("result") or {}
        points = result.get("points") or []
        next_offset = result.get("next_page_offset")

        chunks: list[dict[str, Any]] = []
        for p in points:
            payload = p.get("payload") or {}
            chunks.append(
                {
                    "id": str(p.get("id")),
                    "chunk_index": payload.get("chunk_index"),
                    "text": payload.get("text"),
                    "content_hash": payload.get("content_hash"),
                    "created_at": payload.get("created_at"),
                    "tenant_id": payload.get("tenant_id"),
                    "project_id": payload.get("project_id"),
                    "doc_version": payload.get("doc_version"),
                    "is_finalized": payload.get("is_finalized"),
                    "is_sensitive": payload.get("is_sensitive"),
                }
            )

        return {
            "collection": collection,
            "doc_id": doc_id,
            "chunks": chunks,
            "next_cursor": None if next_offset is None else str(next_offset),
        }

    @r.get("/looking-glass/docs/{doc_id}/chunks/{chunk_index}")
    async def looking_glass_chunk_preview(doc_id: str, chunk_index: int) -> dict[str, Any]:
        collection = _qdrant_collection()
        body: dict[str, Any] = {
            "limit": 1,
            "with_payload": True,
            "with_vector": False,
            "filter": {
                "must": [
                    {"key": "doc_id", "match": {"value": doc_id}},
                    {"key": "chunk_index", "match": {"value": int(chunk_index)}},
                ]
            },
        }
        res = await _qdrant_post_json(f"/collections/{collection}/points/scroll", body)
        result = res.get("result") or {}
        points = result.get("points") or []
        if not points:
            return {"ok": False, "detail": "not found"}
        p = points[0]
        return {
            "ok": True,
            "collection": collection,
            "doc_id": doc_id,
            "chunk_index": int(chunk_index),
            "id": str(p.get("id")),
            "payload": p.get("payload") or {},
        }

    # ------------------------------------------------------------------
    # Metrics aggregation endpoint (Phase 7C)
    # ------------------------------------------------------------------

    @r.get("/looking-glass/metrics")
    def looking_glass_metrics(
        tenant_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
        corpus_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """Aggregated pipeline quality metrics — optionally scoped.

        Returns workflow status distribution, node failure rates, HITL
        escalation rates, and cleanup-feedback category counts.
        """
        with session_factory() as session:
            return _build_metrics(session, tenant_id=tenant_id, project_id=project_id, corpus_id=corpus_id)

    def _build_metrics(
        session: Session,
        *,
        tenant_id: str | None,
        project_id: str | None,
        corpus_id: str | None,
    ) -> dict[str, Any]:
        """Assemble the metrics payload."""

        # --- Workflow run status distribution ---
        run_stmt = select(WorkflowRun.status, func.count()).select_from(WorkflowRun)
        if tenant_id:
            run_stmt = run_stmt.where(WorkflowRun.tenant_id == tenant_id)
        if project_id:
            run_stmt = run_stmt.where(WorkflowRun.project_id == project_id)
        run_stmt = run_stmt.group_by(WorkflowRun.status)
        run_dist = {str(k): int(v) for k, v in session.execute(run_stmt).all() if k is not None}
        run_total = sum(run_dist.values()) or 0

        # --- Node failure rates ---
        node_base = select(NodeRun).join(WorkflowRun, NodeRun.run_id == WorkflowRun.id)
        if tenant_id:
            node_base = node_base.where(WorkflowRun.tenant_id == tenant_id)
        if project_id:
            node_base = node_base.where(WorkflowRun.project_id == project_id)

        node_total_q = select(func.count()).select_from(node_base.subquery())
        node_total = session.execute(node_total_q).scalar_one()

        node_fail_base = node_base.where((NodeRun.status == "failed") | (NodeRun.error_code != ""))
        node_fail_count = session.execute(
            select(func.count()).select_from(node_fail_base.subquery())
        ).scalar_one()

        node_by_name = select(
            NodeRun.node_name, func.count()
        ).join(WorkflowRun, NodeRun.run_id == WorkflowRun.id).where(
            (NodeRun.status == "failed") | (NodeRun.error_code != "")
        )
        if tenant_id:
            node_by_name = node_by_name.where(WorkflowRun.tenant_id == tenant_id)
        if project_id:
            node_by_name = node_by_name.where(WorkflowRun.project_id == project_id)
        node_by_name = node_by_name.group_by(NodeRun.node_name)
        node_fail_by_name = {str(k): int(v) for k, v in session.execute(node_by_name).all() if k is not None}

        # --- HITL escalation ---
        hitl_stmt = select(HitlTaskRow.status, func.count()).select_from(HitlTaskRow)
        if tenant_id:
            hitl_stmt = hitl_stmt.where(HitlTaskRow.tenant_id == tenant_id)
        if project_id:
            hitl_stmt = hitl_stmt.where(HitlTaskRow.project_id == project_id)
        hitl_stmt = hitl_stmt.group_by(HitlTaskRow.status)
        hitl_dist = {str(k): int(v) for k, v in session.execute(hitl_stmt).all() if k is not None}
        hitl_total = sum(hitl_dist.values()) or 0

        # --- Cleanup feedback categories ---
        fb_counts = feedback_category_counts(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            corpus_id=corpus_id,
        )

        # --- Computed rates ---
        completed = run_dist.get("completed", 0)
        failed = run_dist.get("failed", 0)
        auto_accepted = max(0, completed - hitl_total) if completed else 0

        return {
            "scope": {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "corpus_id": corpus_id,
            },
            "workflow_runs": {
                "total": run_total,
                "by_status": run_dist,
                "completion_rate": round(completed / run_total, 4) if run_total else 0,
                "failure_rate": round(failed / run_total, 4) if run_total else 0,
            },
            "node_runs": {
                "total": int(node_total),
                "failed": int(node_fail_count),
                "failure_rate": round(int(node_fail_count) / int(node_total), 4) if node_total else 0,
                "failures_by_node": node_fail_by_name,
            },
            "hitl": {
                "total": hitl_total,
                "by_status": hitl_dist,
                "escalation_rate": round(hitl_total / run_total, 4) if run_total else 0,
            },
            "auto_accepted": {
                "count": auto_accepted,
                "rate": round(auto_accepted / run_total, 4) if run_total else 0,
            },
            "cleanup_feedback": {
                "total": sum(fb_counts.values()),
                "by_category": fb_counts,
            },
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
        store = QdrantStore(url=settings.atlas_qdrant_url, api_key=None, collection=_qdrant_collection())
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
        collection = _qdrant_collection()
        delete_filter: dict[str, Any] = {
            "must": [
                {"key": "tenant_id", "match": {"value": t_id}},
                {"key": "project_id", "match": {"value": p_id}},
                {"key": "doc_id", "match": {"value": doc_id}},
            ]
        }
        res = await _qdrant_post_json(
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
