"""Tenant / Project / Corpus CRUD routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from atlas.models import Corpus, Project, Tenant
from atlas.settings import Settings

from ._helpers import clean_scope_id


# ── Request models ───────────────────────────────────────────────────

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


# ── Route registration ───────────────────────────────────────────────

def register_scope_routes(
    r: APIRouter,
    *,
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    """Register tenant / project / corpus CRUD endpoints on *r*."""

    # ── Tenants ──────────────────────────────────────────────────────

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
        t_id = clean_scope_id("tenant_id", req.tenant_id)
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
        t_id = clean_scope_id("tenant_id", tenant_id)
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

    # ── Projects ─────────────────────────────────────────────────────

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
        t_id = clean_scope_id("tenant_id", req.tenant_id)
        p_id = clean_scope_id("project_id", req.project_id)
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
        t_id = clean_scope_id("tenant_id", tenant_id)
        p_id = clean_scope_id("project_id", project_id)
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

    # ── Corpora ──────────────────────────────────────────────────────

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
        t_id = clean_scope_id("tenant_id", req.tenant_id)
        p_id = clean_scope_id("project_id", req.project_id)
        c_id = clean_scope_id("corpus_id", req.corpus_id)
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
        t_id = clean_scope_id("tenant_id", tenant_id)
        p_id = clean_scope_id("project_id", project_id)
        c_id = clean_scope_id("corpus_id", corpus_id)
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
