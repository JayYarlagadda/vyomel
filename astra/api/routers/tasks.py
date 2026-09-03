"""Task endpoints.

Routers validate and translate. All business logic lives in the orchestrator.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from astra.api.schemas import (
    ActionResponse,
    AuditListResponse,
    AuditRecordResponse,
    CancelRequest,
    CancelResponse,
    CompensationFailureResponse,
    CreateTaskRequest,
    IrreversibleEffectResponse,
    PlanResponse,
    StepResponse,
    TaskListResponse,
    TaskProgress,
    TaskResponse,
)
from astra.core.config import Settings, get_settings
from astra.core.types import TaskStatus
from astra.orchestrator.planning import create_task as create_planned_task
from astra.orchestrator.plans import PlanService
from astra.orchestrator.runtime import get_registry
from astra.orchestrator.tasks import TaskBounds, TaskProgressCounts, TaskService
from astra.security.audit import AuditTrail
from astra.store.db import get_session

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


def _service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TaskService:
    return TaskService(session, settings)


def _to_response(task: object, progress: TaskProgressCounts | None = None) -> TaskResponse:
    body = TaskResponse.model_validate(task)
    if progress is not None:
        body.progress = TaskProgress(
            steps_total=progress.steps_total,
            steps_done=progress.steps_done,
            actions_total=progress.actions_total,
            actions_done=progress.actions_done,
        )
    return body


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: CreateTaskRequest,
    service: Annotated[TaskService, Depends(_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TaskResponse:
    bounds = TaskBounds.from_settings(settings)
    if payload.bounds is not None:
        # Requested bounds may only tighten the configured defaults, which are
        # themselves already capped by the hard ceilings in core.config.
        bounds = TaskBounds(
            max_wall_clock_s=min(
                payload.bounds.max_wall_clock_s or bounds.max_wall_clock_s,
                bounds.max_wall_clock_s,
            ),
            max_token_budget=min(
                payload.bounds.max_token_budget or bounds.max_token_budget,
                bounds.max_token_budget,
            ),
        )

    hints = dict(payload.context_hints)
    if payload.dry_run:
        hints["dry_run"] = True
    task = await create_planned_task(
        session,
        settings,
        get_registry(),
        instruction=payload.instruction,
        origin=payload.origin,
        capability_ceiling=payload.capability_ceiling,
        context_hints=hints,
        bounds=bounds,
        plan_override=payload.plan,
        dry_run=payload.dry_run,
    )
    return _to_response(task, await service.progress(task.id))


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str, service: Annotated[TaskService, Depends(_service)]
) -> TaskResponse:
    task = await service.get(task_id)
    return _to_response(task, await service.progress(task_id))


@router.get("/{task_id}/plan", response_model=PlanResponse)
async def get_plan(
    task_id: str,
    service: Annotated[TaskService, Depends(_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PlanResponse:
    task = await service.get(task_id)
    steps, actions = await PlanService(session, settings, get_registry()).load(task_id)
    return PlanResponse(
        task_id=task.id,
        plan_version=task.plan_version,
        steps=[StepResponse.model_validate(s) for s in steps],
        actions=[ActionResponse.model_validate(a) for a in actions],
    )


@router.get("/{task_id}/actions", response_model=list[ActionResponse])
async def list_actions(
    task_id: str,
    service: Annotated[TaskService, Depends(_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[ActionResponse]:
    await service.get(task_id)
    _steps, actions = await PlanService(session, settings, get_registry()).load(task_id)
    return [ActionResponse.model_validate(a) for a in actions]


@router.get("/{task_id}/audit", response_model=AuditListResponse)
async def task_audit(
    task_id: str,
    service: Annotated[TaskService, Depends(_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> AuditListResponse:
    await service.get(task_id)
    records = await AuditTrail().records(session, task_id=task_id, limit=limit)
    return AuditListResponse(items=[AuditRecordResponse.model_validate(r) for r in records])


@router.post("/{task_id}/cancel", response_model=CancelResponse)
async def cancel_task(
    task_id: str,
    service: Annotated[TaskService, Depends(_service)],
    payload: CancelRequest | None = None,
) -> CancelResponse:
    body = payload or CancelRequest()
    report = await service.cancel(task_id, compensate=body.compensate)
    return CancelResponse(
        task_id=report.task_id,
        status=report.status,
        compensated=report.compensated,
        irreversible=[
            IrreversibleEffectResponse(
                action_id=item.action_id, tool=item.tool, summary=item.summary
            )
            for item in report.irreversible
        ],
        still_running=report.still_running,
        failed=[
            CompensationFailureResponse(action_id=item.action_id, tool=item.tool, error=item.error)
            for item in report.failed
        ],
    )


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    service: Annotated[TaskService, Depends(_service)],
    task_status: Annotated[TaskStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> TaskListResponse:
    tasks = await service.list(status=task_status, limit=limit)
    return TaskListResponse(items=[_to_response(t) for t in tasks])
