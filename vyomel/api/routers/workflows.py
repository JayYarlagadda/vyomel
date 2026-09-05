"""Learned / saved workflows (docs/04 section 5, FR-901-903)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from vyomel.api.schemas import (
    InvokeWorkflowRequest,
    InvokeWorkflowResponse,
    MineWorkflowsRequest,
    MineWorkflowsResponse,
    WorkflowListResponse,
    WorkflowOut,
    WorkflowParameterOut,
    WorkflowStepOut,
)
from vyomel.core.config import Settings, get_settings
from vyomel.core.errors import ErrorCode
from vyomel.learning.pg_store import (
    PostgresWorkflowStore,
    accept_workflow_pg,
    reject_workflow_pg,
)
from vyomel.learning.proposal import WorkflowProposal, bind_parameters
from vyomel.learning.service import actions_from_records, mine_and_propose, mine_and_propose_pg
from vyomel.learning.store import (
    WorkflowError,
    WorkflowNotFoundError,
    accept_workflow,
    expand_workflow,
    get_workflow_store,
    reject_workflow,
)
from vyomel.orchestrator.runtime import get_registry
from vyomel.store.db import get_session

router = APIRouter(prefix="/v1/workflows", tags=["workflows"])


def _out(proposal: WorkflowProposal) -> WorkflowOut:
    return WorkflowOut(
        id=proposal.id,
        name=proposal.name,
        description=proposal.description,
        source=proposal.source,
        status=proposal.status,
        occurrence_count=proposal.occurrence_count,
        trust_level=proposal.trust_level,
        pattern_key=proposal.pattern_key,
        definition=[
            WorkflowStepOut(alias=s.alias, tool=s.tool, parameters=s.parameters)
            for s in proposal.definition
        ],
        parameters=[
            WorkflowParameterOut(name=p.name, description=p.description, example=p.example)
            for p in proposal.parameters
        ],
        supporting_task_ids=list(proposal.supporting_task_ids),
    )


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


@router.get("", response_model=WorkflowListResponse)
async def list_workflows(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(_settings)],
    status: Annotated[str | None, Query()] = None,
) -> WorkflowListResponse:
    if settings.workflow_store_backend == "postgres":
        items = await PostgresWorkflowStore(session).list(status=status)
    else:
        items = get_workflow_store().list(status=status)
    return WorkflowListResponse(items=[_out(item) for item in items])


@router.post("/mine", response_model=MineWorkflowsResponse)
async def mine_workflows(
    payload: MineWorkflowsRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(_settings)],
) -> MineWorkflowsResponse:
    caps = {spec.name: spec.base_capability for spec in get_registry().catalog()}
    actions = actions_from_records(payload.actions)
    if settings.workflow_store_backend == "postgres":
        proposals = await mine_and_propose_pg(
            session,
            actions,
            min_support=payload.min_support,
            tool_capabilities=caps,
        )
    else:
        proposals = mine_and_propose(
            actions, min_support=payload.min_support, tool_capabilities=caps
        )
    return MineWorkflowsResponse(proposals=[_out(p) for p in proposals])


@router.post("/{workflow_id}/accept", response_model=WorkflowOut)
async def accept(
    workflow_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(_settings)],
) -> WorkflowOut:
    if settings.workflow_store_backend == "postgres":
        return _out(await accept_workflow_pg(PostgresWorkflowStore(session), workflow_id))
    return _out(accept_workflow(get_workflow_store(), workflow_id))


@router.post("/{workflow_id}/reject", response_model=WorkflowOut)
async def reject(
    workflow_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(_settings)],
) -> WorkflowOut:
    if settings.workflow_store_backend == "postgres":
        return _out(await reject_workflow_pg(PostgresWorkflowStore(session), workflow_id))
    return _out(reject_workflow(get_workflow_store(), workflow_id))


@router.post("/{workflow_id}/invoke", response_model=InvokeWorkflowResponse)
async def invoke(
    workflow_id: str,
    payload: InvokeWorkflowRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(_settings)],
) -> InvokeWorkflowResponse:
    if settings.workflow_store_backend == "postgres":
        store = PostgresWorkflowStore(session)
        proposal = await store.get(workflow_id)
        if proposal is None:
            raise WorkflowNotFoundError(f"unknown workflow: {workflow_id}")
        if proposal.status != "accepted":
            raise WorkflowError(
                "workflow is not accepted and cannot be invoked",
                detail={"status": proposal.status, "workflow_id": workflow_id},
                code=ErrorCode.PERMISSION_DENIED,
            )
        try:
            steps = bind_parameters(proposal, payload.parameters)
        except ValueError as exc:
            raise WorkflowError(str(exc)) from exc
    else:
        mem = get_workflow_store()
        proposal = mem.get(workflow_id)
        steps = expand_workflow(mem, workflow_id, payload.parameters)
        assert proposal is not None
    return InvokeWorkflowResponse(
        workflow_id=proposal.id,
        name=proposal.name,
        trust_level=proposal.trust_level,
        steps=steps,
    )


@router.get("/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(
    workflow_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(_settings)],
) -> WorkflowOut:
    if settings.workflow_store_backend == "postgres":
        proposal = await PostgresWorkflowStore(session).get(workflow_id)
    else:
        proposal = get_workflow_store().get(workflow_id)
    if proposal is None:
        raise WorkflowNotFoundError(f"unknown workflow: {workflow_id}")
    return _out(proposal)
