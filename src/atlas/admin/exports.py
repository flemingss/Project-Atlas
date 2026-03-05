"""Export and import routes (document, corpus, project, tenant)."""

from __future__ import annotations

import io
from typing import Any

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, sessionmaker

from atlas.config_manager import ConfigManager
from atlas.settings import Settings


def register_export_routes(
    router: APIRouter,
    *,
    session_factory: sessionmaker[Session],
    config_manager: ConfigManager,
    settings: Settings,
) -> None:

    @router.get("/docs/{doc_id}/export")
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

    @router.get("/corpora/{corpus_id}/export")
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

    @router.get("/projects/{project_id}/export")
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

    @router.get("/tenants/{tenant_id}/export")
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

    @router.get("/export")
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

    @router.post("/corpora/{corpus_id}/import")
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
