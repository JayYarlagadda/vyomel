"""Task endpoints.

Routers validate and translate. All business logic lives in the orchestrator.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from astra.api.schemas import (
    CreateTaskRequest,
    TaskListResponse,
    TaskResponse,
)
from astra.core.config import Settings, get_settings
from astra.core.types import TaskStatus
from astra.orchestrator.tasks import TaskBounds, TaskService
from astra.store.db import get_session

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


def _service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TaskService:
    return TaskService(session, settings)


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: CreateTaskRequest,
    service: Annotated[TaskService, Depends(_service)],
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

    task = await service.create(
        instruction=payload.instruction,
        capability_ceiling=payload.capability_ceiling,
        context_hints=payload.context_hints,
        bounds=bounds,
    )
    return TaskResponse.model_validate(task)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str, service: Annotated[TaskService, Depends(_service)]
) -> TaskResponse:
    return TaskResponse.model_validate(await service.get(task_id))


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    service: Annotated[TaskService, Depends(_service)],
    task_status: Annotated[TaskStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> TaskListResponse:
    tasks = await service.list(status=task_status, limit=limit)
    return TaskListResponse(items=[TaskResponse.model_validate(t) for t in tasks])
