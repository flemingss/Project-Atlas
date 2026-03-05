"""Human-in-the-loop (HITL) task management routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy.orm import Session, sessionmaker

from atlas.config_manager import ConfigManager
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
from atlas.pipeline.runner import resume_completed_hitl_task
from atlas.workflow_ledger import get_workflow_run


def register_hitl_routes(
    router: APIRouter,
    *,
    session_factory: sessionmaker[Session],
    config_manager: ConfigManager,
) -> None:

    @router.get("/hitl/tasks", response_model=list[HitlTaskResponse])
    def hitl_tasks(
        status: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        tenant_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
    ) -> list[HitlTaskResponse]:
        with session_factory() as session:
            rows = list_hitl_tasks(session, status=status, limit=int(limit), tenant_id=tenant_id, project_id=project_id)
        return [to_hitl_response(t) for t in rows]

    @router.post("/hitl/tasks", response_model=HitlTaskResponse)
    def create_hitl(req: HitlTaskCreateRequest) -> HitlTaskResponse:
        with session_factory() as session:
            if get_workflow_run(session, run_id=req.run_id) is None:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="run not found")
            row = create_hitl_task(session, req=req)
        return to_hitl_response(row)

    @router.get("/hitl/tasks/{task_id}", response_model=HitlTaskResponse)
    def hitl_task_detail(task_id: int) -> HitlTaskResponse:
        with session_factory() as session:
            row = get_hitl_task(session, task_id=task_id)
        if row is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="task not found")
        return to_hitl_response(row)

    @router.post("/hitl/tasks/next", response_model=HitlTaskResponse | None)
    def hitl_next(assigned_to: str = "") -> HitlTaskResponse | None:
        with session_factory() as session:
            row = claim_next_task(session, assigned_to=assigned_to)
        return None if row is None else to_hitl_response(row)

    @router.post("/hitl/tasks/{task_id}/complete", response_model=HitlTaskResponse)
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

    @router.post("/hitl/tasks/{task_id}/resume")
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

    @router.post("/hitl/tasks/{task_id}/skip", response_model=HitlTaskResponse)
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

    @router.post("/hitl/tasks/{task_id}/reject", response_model=HitlTaskResponse)
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
