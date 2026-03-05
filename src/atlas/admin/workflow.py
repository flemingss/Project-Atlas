"""Workflow run, node-run, and artifact CRUD routes."""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy.orm import Session, sessionmaker

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


def register_workflow_routes(
    router: APIRouter,
    *,
    session_factory: sessionmaker[Session],
) -> None:

    @router.get("/runs", response_model=list[WorkflowRunResponse])
    def runs(
        limit: int = Query(default=100, ge=1, le=500),
        tenant_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
    ) -> list[WorkflowRunResponse]:
        with session_factory() as session:
            rows = list_workflow_runs(session, limit=int(limit), tenant_id=tenant_id, project_id=project_id)
        return [to_run_response(r) for r in rows]

    @router.post("/runs", response_model=WorkflowRunResponse)
    def create_run(req: WorkflowRunCreateRequest) -> WorkflowRunResponse:
        with session_factory() as session:
            row = create_workflow_run(session, req=req)
        return to_run_response(row)

    @router.get("/runs/{run_id}", response_model=WorkflowRunResponse)
    def run_detail(run_id: int) -> WorkflowRunResponse:
        with session_factory() as session:
            row = get_workflow_run(session, run_id=run_id)
        if row is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="run not found")
        return to_run_response(row)

    @router.get("/runs/{run_id}/node-runs", response_model=list[NodeRunResponse])
    def node_runs(run_id: int, limit: int = Query(default=500, ge=1, le=2000)) -> list[NodeRunResponse]:
        with session_factory() as session:
            rows = list_node_runs(session, run_id=run_id, limit=int(limit))
        return [to_node_run_response(n) for n in rows]

    @router.post("/runs/{run_id}/node-runs", response_model=NodeRunResponse)
    def create_node_run_endpoint(run_id: int, req: NodeRunCreateRequest) -> NodeRunResponse:
        with session_factory() as session:
            # Validate run exists.
            if get_workflow_run(session, run_id=run_id) is None:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="run not found")
            row = create_node_run(session, run_id=run_id, req=req)
        return to_node_run_response(row)

    @router.get("/runs/{run_id}/artifacts", response_model=list[ArtifactRefResponse])
    def artifacts(run_id: int, limit: int = Query(default=500, ge=1, le=2000)) -> list[ArtifactRefResponse]:
        with session_factory() as session:
            rows = list_artifact_refs(session, run_id=run_id, limit=int(limit))
        return [to_artifact_ref_response(a) for a in rows]

    @router.post("/runs/{run_id}/artifacts", response_model=ArtifactRefResponse)
    def add_artifact(run_id: int, req: ArtifactRefCreateRequest) -> ArtifactRefResponse:
        with session_factory() as session:
            if get_workflow_run(session, run_id=run_id) is None:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="run not found")
            row = add_artifact_ref(session, run_id=run_id, req=req)
        return to_artifact_ref_response(row)
