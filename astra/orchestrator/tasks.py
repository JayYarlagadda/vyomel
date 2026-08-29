"""Task service.

The only layer permitted to mutate task state. API routers call into here; they
never touch the database directly (docs/02-ARCHITECTURE.md section 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from astra.core.clock import Clock, SystemClock
from astra.core.config import Settings
from astra.core.errors import NotFoundError
from astra.core.ids import new_id
from astra.core.types import ActionStatus, Capability, StepStatus, TaskOrigin, TaskStatus
from astra.runtime.cancel import Canceller, CancelReport
from astra.security.audit import AuditEvent, AuditTrail
from astra.store.models import Action, Step, Task


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


@dataclass(frozen=True, slots=True)
class TaskProgressCounts:
    steps_total: int = 0
    steps_done: int = 0
    actions_total: int = 0
    actions_done: int = 0


class TaskService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        clock: Clock | None = None,
        audit: AuditTrail | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._clock = clock or SystemClock()
        self._audit = audit or AuditTrail(self._clock)

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
        await self._audit.append(
            self._session,
            actor=f"origin:{origin.value}",
            event_type=AuditEvent.TASK_CREATED,
            task_id=task.id,
            capability_level=capability_ceiling,
            payload={
                "instruction": instruction,
                "context_hints": task.context_hints,
                "capability_ceiling": capability_ceiling.value,
                "deadline_at": task.deadline_at.isoformat() if task.deadline_at else None,
            },
        )
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

    async def progress(self, task_id: str) -> TaskProgressCounts:
        steps_total = await self._session.scalar(
            select(func.count()).select_from(Step).where(Step.task_id == task_id)
        )
        steps_done = await self._session.scalar(
            select(func.count())
            .select_from(Step)
            .where(Step.task_id == task_id, Step.status == StepStatus.SUCCEEDED)
        )
        actions_total = await self._session.scalar(
            select(func.count()).select_from(Action).where(Action.task_id == task_id)
        )
        actions_done = await self._session.scalar(
            select(func.count())
            .select_from(Action)
            .where(Action.task_id == task_id, Action.status == ActionStatus.SUCCEEDED)
        )
        return TaskProgressCounts(
            steps_total=int(steps_total or 0),
            steps_done=int(steps_done or 0),
            actions_total=int(actions_total or 0),
            actions_done=int(actions_done or 0),
        )

    async def cancel(
        self,
        task_id: str,
        *,
        compensate: bool = True,
        actor: str = "user",
    ) -> CancelReport:
        """Cancel a running task and compensate reversible SUCCEEDED actions.

        The runtime owns reverse-topo ordering and the ``compensate()`` calls;
        this method is the orchestrator seam the API and CLI go through.
        """
        from astra.orchestrator.runtime import get_registry

        await self.get(task_id)
        return await Canceller(
            self._settings,
            get_registry(),
            clock=self._clock,
            audit=self._audit,
        ).cancel(self._session, task_id, compensate=compensate, actor=actor)
