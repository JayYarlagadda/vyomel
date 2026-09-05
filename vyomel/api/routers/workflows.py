"""Learned / saved workflows (docs/04 section 5, FR-901-903)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

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
from vyomel.learning.proposal import WorkflowProposal
from vyomel.learning.service import actions_from_records, mine_and_propose
from vyomel.learning.store import (
    accept_workflow,
    expand_workflow,
    get_workflow_store,
    reject_workflow,
)
from vyomel.orchestrator.runtime import get_registry

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


@router.get("", response_model=WorkflowListResponse)
async def list_workflows(
    status: Annotated[str | None, Query()] = None,
) -> WorkflowListResponse:
    items = get_workflow_store().list(status=status)
    return WorkflowListResponse(items=[_out(item) for item in items])


@router.post("/mine", response_model=MineWorkflowsResponse)
async def mine_workflows(payload: MineWorkflowsRequest) -> MineWorkflowsResponse:
    caps = {spec.name: spec.base_capability for spec in get_registry().catalog()}
    proposals = mine_and_propose(
        actions_from_records(payload.actions),
        min_support=payload.min_support,
        tool_capabilities=caps,
    )
    return MineWorkflowsResponse(proposals=[_out(p) for p in proposals])


@router.post("/{workflow_id}/accept", response_model=WorkflowOut)
async def accept(workflow_id: str) -> WorkflowOut:
    return _out(accept_workflow(get_workflow_store(), workflow_id))


@router.post("/{workflow_id}/reject", response_model=WorkflowOut)
async def reject(workflow_id: str) -> WorkflowOut:
    return _out(reject_workflow(get_workflow_store(), workflow_id))


@router.post("/{workflow_id}/invoke", response_model=InvokeWorkflowResponse)
async def invoke(workflow_id: str, payload: InvokeWorkflowRequest) -> InvokeWorkflowResponse:
    store = get_workflow_store()
    proposal = store.get(workflow_id)
    steps = expand_workflow(store, workflow_id, payload.parameters)
    assert proposal is not None
    return InvokeWorkflowResponse(
        workflow_id=proposal.id,
        name=proposal.name,
        trust_level=proposal.trust_level,
        steps=steps,
    )


@router.get("/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(workflow_id: str) -> WorkflowOut:
    from vyomel.learning.store import WorkflowNotFoundError

    proposal = get_workflow_store().get(workflow_id)
    if proposal is None:
        raise WorkflowNotFoundError(f"unknown workflow: {workflow_id}")
    return _out(proposal)
