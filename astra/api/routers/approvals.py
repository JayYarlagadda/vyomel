"""Approval endpoints (FR-303, FR-304, FR-305).

The queue is the human-in-the-loop surface: everything a user needs to consent
to an action, and one endpoint to answer. All three answers go through
``POST /decide`` rather than three verbs, because they are one decision with one
race to lose — two clients answering the same approval must not both win, and
that is enforced in one place.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from astra.api.schemas import (
    ApprovalListResponse,
    ApprovalResponse,
    DecideApprovalRequest,
)
from astra.core.config import Settings, get_settings
from astra.core.types import ApprovalStatus
from astra.orchestrator.approvals import ApprovalWorkflow
from astra.orchestrator.runtime import get_registry
from astra.store.db import get_session

router = APIRouter(prefix="/v1/approvals", tags=["approvals"])


def _workflow(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ApprovalWorkflow:
    return ApprovalWorkflow(session, settings, get_registry())


@router.get("", response_model=ApprovalListResponse)
async def list_approvals(
    workflow: Annotated[ApprovalWorkflow, Depends(_workflow)],
    approval_status: Annotated[ApprovalStatus | None, Query(alias="status")] = (
        ApprovalStatus.PENDING
    ),
    task_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ApprovalListResponse:
    approvals = await workflow.list(status=approval_status, task_id=task_id, limit=limit)
    return ApprovalListResponse(items=[ApprovalResponse.model_validate(a) for a in approvals])


@router.get("/{approval_id}", response_model=ApprovalResponse)
async def get_approval(
    approval_id: str, workflow: Annotated[ApprovalWorkflow, Depends(_workflow)]
) -> ApprovalResponse:
    return ApprovalResponse.model_validate(await workflow.get(approval_id))


@router.post("/{approval_id}/decide", response_model=ApprovalResponse)
async def decide_approval(
    approval_id: str,
    payload: DecideApprovalRequest,
    workflow: Annotated[ApprovalWorkflow, Depends(_workflow)],
) -> ApprovalResponse:
    if payload.decision == "REJECTED":
        approval = await workflow.reject(
            approval_id, decided_by=payload.decided_by, reason=payload.reason
        )
    else:
        approval = await workflow.approve(
            approval_id,
            decided_by=payload.decided_by,
            modified_parameters=payload.parameters,
        )
    return ApprovalResponse.model_validate(approval)
