"""Task service.

The only layer permitted to mutate task state. API routers call into here; they
never touch the database directly (docs/02-ARCHITECTURE.md section 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from astra.core.clock import Clock, SystemClock
from astra.core.config import Settings
from astra.core.errors import NotFoundError
from astra.core.ids import new_id
from astra.core.types import Capability, TaskOrigin, TaskStatus
from astra.store.models import Task


@dataclass(frozen=True, slots=True)
class TaskBounds:
    """Per-task execution bounds, already validated against the hard ceilings."""

    max_wall_clock_s: int
    max_token_budget: int

    @classmethod
    def from_settings(cls, settings: Settings) -> TaskBounds:
        return cls(
            max_wall_clock_s=settings.max_wall_clock_s,
            max_token_budget=settings.max_token_budget,
        )


class TaskService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        clock: Clock | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._clock = clock or SystemClock()

    async def create(
        self,
        *,
        instruction: str,
        origin: TaskOrigin = TaskOrigin.API,
        capability_ceiling: Capability = Capability.L2,
        context_hints: dict[str, Any] | None = None,
        bounds: TaskBounds | None = None,
    ) -> Task:
        """Persist a task before anything else happens to it.

        The row is committed by the caller's unit of work prior to any planning
        or dispatch, so a crash between creation and planning leaves a
        recoverable record rather than a lost request (FR-201).
        """
        effective = bounds or TaskBounds.from_settings(self._settings)
        now = self._clock.now()

        task = Task(
            id=new_id(),
            instruction=instruction,
            status=TaskStatus.CREATED,
            origin=origin,
            capability_ceiling=capability_ceiling,
            context_hints=context_hints or {},
            token_budget=effective.max_token_budget,
            max_wall_clock_s=effective.max_wall_clock_s,
            deadline_at=now + timedelta(seconds=effective.max_wall_clock_s),
        )
        self._session.add(task)
        await self._session.flush()
        return task

    async def get(self, task_id: str) -> Task:
        task = await self._session.get(Task, task_id)
        if task is None:
            raise NotFoundError(f"Task {task_id} not found", detail={"task_id": task_id})
        return task

    async def list(self, *, status: TaskStatus | None = None, limit: int = 50) -> list[Task]:
        stmt = select(Task).order_by(Task.id.desc()).limit(limit)
        if status is not None:
            stmt = stmt.where(Task.status == status)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
