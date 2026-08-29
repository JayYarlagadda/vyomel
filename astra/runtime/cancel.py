"""Task cancellation and reverse-topo compensation (07 §8, FR-209).

On cancel:

1. The task moves to ``CANCELLED`` so the dispatcher will not start more work.
2. Every not-yet-started action (``PLANNED`` / ``READY`` / ``DISPATCHED`` /
   ``WAITING_FOR_USER``) moves to ``CANCELLED``.
3. ``SUCCEEDED`` actions with ``reversible=True`` are compensated in
   ``reverse_topo`` order — the same function the DAG module already owns.
   Do not invent a second ordering.
4. Irreversible completed actions are listed, not pretended undone.
5. ``RUNNING`` actions are signalled, not seized. The worker holds a
   per-action ``CancellationToken`` and, after ``cancel_grace_s``, cancels
   the execute task and CAS-es ``RUNNING → CANCELLED``. The canceller must
   not take that CAS: a worker that already mutated the world but has not
   committed ``SUCCEEDED`` would leave an effect that is neither recorded
   nor compensated.

Each compensation is an audited action. The ``compensate()`` on a tool is
mandatory when ``reversible=True``; this module is the thing that actually
calls it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from astra.core.cancel import CancellationToken
from astra.core.clock import Clock, SystemClock
from astra.core.config import Settings
from astra.core.errors import ConflictError, NotFoundError
from astra.core.logging import get_logger
from astra.core.types import ActionStatus, StepStatus, TaskStatus
from astra.runtime.dag import ActionNode, reverse_topo
from astra.runtime.state import ActionTrigger, TaskTrigger, apply_action, apply_task
from astra.security.audit import AuditEvent, AuditTrail
from astra.store.models import Action, Step, Task
from astra.store.repos import ActionRepo, StepRepo, TaskRepo
from astra.tools.base import ToolContext
from astra.tools.registry import RegistryError, ToolRegistry

log = get_logger(__name__)

_CANCEL_NOW = frozenset(
    {
        ActionStatus.PLANNED,
        ActionStatus.READY,
        ActionStatus.DISPATCHED,
        ActionStatus.WAITING_FOR_USER,
    }
)


@dataclass(frozen=True, slots=True)
class IrreversibleEffect:
    action_id: str
    tool: str
    summary: str


@dataclass(frozen=True, slots=True)
class CompensationFailure:
    action_id: str
    tool: str
    error: str


@dataclass(frozen=True, slots=True)
class CancelReport:
    task_id: str
    status: TaskStatus
    compensated: list[str] = field(default_factory=list)
    irreversible: list[IrreversibleEffect] = field(default_factory=list)
    still_running: list[str] = field(default_factory=list)
    failed: list[CompensationFailure] = field(default_factory=list)

    def as_payload(self) -> dict[str, Any]:
        return {
            "compensated": list(self.compensated),
            "irreversible": [
                {"action_id": item.action_id, "tool": item.tool, "summary": item.summary}
                for item in self.irreversible
            ],
            "still_running": list(self.still_running),
            "failed": [
                {"action_id": item.action_id, "tool": item.tool, "error": item.error}
                for item in self.failed
            ],
        }


class Canceller:
    def __init__(
        self,
        settings: Settings,
        registry: ToolRegistry,
        *,
        clock: Clock | None = None,
        audit: AuditTrail | None = None,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._clock = clock or SystemClock()
        self._audit = audit or AuditTrail(self._clock)

    async def cancel(
        self,
        session: AsyncSession,
        task_id: str,
        *,
        compensate: bool = True,
        actor: str = "user",
    ) -> CancelReport:
        repo = TaskRepo(session)
        task = await repo.get(task_id)
        if task is None:
            raise NotFoundError(f"Task {task_id} not found", detail={"task_id": task_id})

        action_repo = ActionRepo(session)
        step_repo = StepRepo(session)
        actions = await action_repo.list_by_task(task_id)
        steps = await step_repo.list_by_task(task_id)

        if task.status is TaskStatus.CANCELLED:
            return _report_from(task, actions, compensate=compensate)

        if task.status.is_terminal:
            raise ConflictError(
                f"Task {task_id} is already {task.status.value} and cannot be cancelled",
                detail={"task_id": task_id, "status": task.status.value},
            )

        now = self._clock.now()
        dest = apply_task(task.status, TaskTrigger.CANCELLED)
        moved = await repo.cas_status(task.id, expected=task.status, new=dest, finished_at=now)
        if moved is None:
            # Lost the race with completion. Re-read and either return or conflict.
            task = await repo.get(task_id)
            if task is None:
                raise NotFoundError(f"Task {task_id} not found", detail={"task_id": task_id})
            if task.status is TaskStatus.CANCELLED:
                actions = await action_repo.list_by_task(task_id)
                return _report_from(task, actions, compensate=compensate)
            raise ConflictError(
                f"Task {task_id} is already {task.status.value} and cannot be cancelled",
                detail={"task_id": task_id, "status": task.status.value},
            )
        task = moved

        for action in actions:
            if action.status in _CANCEL_NOW:
                cancelled = apply_action(action.status, ActionTrigger.TASK_CANCELLED)
                await action_repo.cas_status(
                    action.id,
                    expected=action.status,
                    new=cancelled,
                    finished_at=now,
                    lease_owner=None,
                    lease_until=None,
                )

        # Workers in other sessions (or processes) observe the cancelled task
        # row. Holding that write uncommitted for the grace wait would mean
        # they never see the signal.
        await session.commit()

        actions = await action_repo.list_by_task(task_id)
        compensated: list[str] = []
        irreversible: list[IrreversibleEffect] = []
        failed: list[CompensationFailure] = []

        if compensate:
            compensated, irreversible, failed = await self._compensate_succeeded(
                session, actions, actor=actor
            )
            actions = await action_repo.list_by_task(task_id)
        else:
            irreversible = [
                IrreversibleEffect(
                    action_id=action.id,
                    tool=action.tool,
                    summary=f"{action.tool} was left in place (compensate=false)",
                )
                for action in actions
                if action.status is ActionStatus.SUCCEEDED
            ]

        still_running = [a.id for a in actions if a.status is ActionStatus.RUNNING]
        if still_running and self._settings.cancel_grace_s > 0:
            # Give the worker time to observe the task status, set the
            # per-action token, and abandon the execute. Then compensate
            # anything that committed SUCCEEDED in that window.
            await asyncio.sleep(self._settings.cancel_grace_s)
            for row in actions:
                session.expire(row)
            actions = await action_repo.list_by_task(task_id)
            if compensate:
                extra_ok, extra_irreversible, extra_failed = await self._compensate_succeeded(
                    session, actions, actor=actor
                )
                compensated.extend(extra_ok)
                irreversible.extend(extra_irreversible)
                failed.extend(extra_failed)
                actions = await action_repo.list_by_task(task_id)
            still_running = [a.id for a in actions if a.status is ActionStatus.RUNNING]
        await _sync_cancelled_steps(step_repo, steps, actions)

        report = CancelReport(
            task_id=task.id,
            status=task.status,
            compensated=compensated,
            irreversible=irreversible,
            still_running=still_running,
            failed=failed,
        )
        await self._audit.append(
            session,
            actor=actor,
            event_type=AuditEvent.TASK_CANCELLED,
            task_id=task.id,
            payload=report.as_payload(),
        )
        log.info(
            "astra.runtime.task_cancelled",
            task_id=task.id,
            compensated=len(compensated),
            irreversible=len(irreversible),
            still_running=len(still_running),
        )
        return report

    async def _compensate_succeeded(
        self,
        session: AsyncSession,
        actions: list[Action],
        *,
        actor: str,
    ) -> tuple[list[str], list[IrreversibleEffect], list[CompensationFailure]]:
        by_id = {action.id: action for action in actions}
        nodes = [
            ActionNode(
                id=action.id,
                status=action.status,
                depends_on=tuple(action.depends_on),
                step_id=action.step_id,
            )
            for action in actions
        ]
        order = reverse_topo(nodes)
        repo = ActionRepo(session)
        now = self._clock.now()
        compensated: list[str] = []
        irreversible: list[IrreversibleEffect] = []
        failed: list[CompensationFailure] = []

        for action_id in order:
            action = by_id[action_id]
            if action.status is not ActionStatus.SUCCEEDED:
                continue
            if not action.reversible:
                irreversible.append(
                    IrreversibleEffect(
                        action_id=action.id,
                        tool=action.tool,
                        summary=_irreversible_summary(action),
                    )
                )
                continue
            try:
                await self._compensate_one(action)
            except Exception as exc:
                log.exception(
                    "astra.runtime.compensate_failed", action_id=action.id, tool=action.tool
                )
                failed.append(
                    CompensationFailure(
                        action_id=action.id,
                        tool=action.tool,
                        error=str(exc)[:400],
                    )
                )
                continue
            dest = apply_action(ActionStatus.SUCCEEDED, ActionTrigger.COMPENSATED)
            await repo.cas_status(
                action.id,
                expected=ActionStatus.SUCCEEDED,
                new=dest,
                finished_at=now,
            )
            await self._audit.append(
                session,
                actor=actor,
                event_type=AuditEvent.ACTION_COMPENSATED,
                task_id=action.task_id,
                action_id=action.id,
                capability_level=action.capability_level,
                payload={"tool": action.tool},
            )
            compensated.append(action.id)

        return compensated, irreversible, failed

    async def _compensate_one(self, action: Action) -> None:
        try:
            tool = self._registry.get(action.tool)
        except RegistryError as exc:
            raise RuntimeError(f"unknown tool {action.tool}") from exc
        try:
            params = tool.Input.model_validate(action.parameters)
            result = tool.Output.model_validate(action.result or {})
        except ValidationError as exc:
            raise RuntimeError(f"cannot rehydrate {action.tool} payload") from exc
        ctx = ToolContext(
            task_id=action.task_id,
            action_id=action.id,
            capability_granted=action.capability_level,
            scratch_dir=self._settings.scratch_dir,
            allowed_roots=list(self._settings.allowed_roots),
            deadline=self._clock.now() + timedelta(seconds=action.timeout_s),
            cancel=CancellationToken(),
            clock=self._clock,
            trash_dir=self._settings.trash_dir,
        )
        await tool.compensate(params, result, ctx)


def _irreversible_summary(action: Action) -> str:
    return f"{action.tool} already ran and cannot be undone"


def _report_from(task: Task, actions: list[Action], *, compensate: bool) -> CancelReport:
    return CancelReport(
        task_id=task.id,
        status=task.status,
        compensated=[a.id for a in actions if a.status is ActionStatus.ROLLED_BACK],
        irreversible=[
            IrreversibleEffect(
                action_id=a.id,
                tool=a.tool,
                summary=_irreversible_summary(a)
                if compensate
                else f"{a.tool} was left in place (compensate=false)",
            )
            for a in actions
            if a.status is ActionStatus.SUCCEEDED
        ],
        still_running=[a.id for a in actions if a.status is ActionStatus.RUNNING],
    )


async def _sync_cancelled_steps(
    step_repo: StepRepo, steps: list[Step], actions: list[Action]
) -> None:
    by_step: dict[str, list[Action]] = {}
    for action in actions:
        by_step.setdefault(action.step_id, []).append(action)
    dropped = {ActionStatus.CANCELLED, ActionStatus.ROLLED_BACK}
    for step in steps:
        group = by_step.get(step.id, [])
        if group and all(a.status in dropped for a in group):
            await step_repo.set_status(step.id, StepStatus.SKIPPED)
