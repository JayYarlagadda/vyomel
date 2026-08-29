"""Thin repositories. Domain rules live in runtime/orchestrator, not here."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from astra.core.ids import new_id
from astra.core.types import ActionStatus, Capability, StepStatus, TaskStatus
from astra.store.models import Action, DeadLetter, SideEffectLedger, Step, Task
from astra.store.models import Verification as VerificationRow


class ActionRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, action_id: str) -> Action | None:
        return await self._session.get(Action, action_id)

    async def list_by_task(self, task_id: str) -> list[Action]:
        result = await self._session.execute(select(Action).where(Action.task_id == task_id))
        return list(result.scalars().all())

    async def cas_dispatch(self, action_id: str, *, now: datetime) -> Action | None:
        result = await self._session.execute(
            update(Action)
            .where(Action.id == action_id, Action.status == ActionStatus.READY)
            .values(
                status=ActionStatus.DISPATCHED,
                dispatched_at=now,
                lease_owner=None,
                lease_until=None,
            )
            .returning(Action)
        )
        return result.scalar_one_or_none()

    async def cas_claim(
        self,
        action_id: str,
        *,
        worker_id: str,
        lease_until: datetime,
        now: datetime,
    ) -> Action | None:
        result = await self._session.execute(
            update(Action)
            .where(
                Action.id == action_id,
                Action.status.in_((ActionStatus.DISPATCHED, ActionStatus.READY)),
            )
            .values(
                status=ActionStatus.RUNNING,
                lease_owner=worker_id,
                lease_until=lease_until,
                started_at=now,
                attempt_count=Action.attempt_count + 1,
            )
            .returning(Action)
        )
        return result.scalar_one_or_none()

    async def cas_status(
        self,
        action_id: str,
        *,
        expected: ActionStatus,
        new: ActionStatus,
        **fields: Any,
    ) -> Action | None:
        result = await self._session.execute(
            update(Action)
            .where(Action.id == action_id, Action.status == expected)
            .values(status=new, **fields)
            .returning(Action)
        )
        return result.scalar_one_or_none()

    async def expired_leases(self, now: datetime) -> list[Action]:
        result = await self._session.execute(
            select(Action).where(
                Action.status.in_((ActionStatus.DISPATCHED, ActionStatus.RUNNING)),
                Action.lease_until.is_not(None),
                Action.lease_until < now,
            )
        )
        return list(result.scalars().all())

    async def dispatchable(self, *, now: datetime, limit: int) -> list[Action]:
        stmt: Select[tuple[Action]] = (
            select(Action)
            .where(
                Action.status == ActionStatus.READY,
                (Action.available_at.is_(None)) | (Action.available_at <= now),
            )
            .order_by(Action.id)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def orphan_dispatched(self) -> list[Action]:
        """DISPATCHED with no lease: committed, never claimed. Redis may have lost them."""
        result = await self._session.execute(
            select(Action).where(
                Action.status == ActionStatus.DISPATCHED,
                Action.lease_until.is_(None),
            )
        )
        return list(result.scalars().all())

    async def in_flight_count(self, task_id: str) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(Action)
            .where(
                Action.task_id == task_id,
                Action.status.in_((ActionStatus.DISPATCHED, ActionStatus.RUNNING)),
            )
        )
        return int(result.scalar_one())

    async def rewrite_invocation(
        self,
        action_id: str,
        *,
        parameters: dict[str, Any],
        capability_level: Capability,
        idempotency_key: str,
    ) -> Action | None:
        """Replace an action's parameters after an approved modification.

        The three fields move together on purpose. Parameters determine the
        capability level and the idempotency key, so writing one without the
        others would leave the row describing an invocation that was never
        classified, or let a replay of the edited action collide with the
        original's side-effect reservation.
        """
        result = await self._session.execute(
            update(Action)
            .where(Action.id == action_id, Action.status == ActionStatus.WAITING_FOR_USER)
            .values(
                parameters=parameters,
                capability_level=capability_level,
                idempotency_key=idempotency_key,
            )
            .returning(Action)
        )
        return result.scalar_one_or_none()

    async def insert_ledger(self, *, key: str, tool: str, action_id: str) -> bool:
        """Reserve a side-effect slot. Returns False if the key is already taken."""
        try:
            async with self._session.begin_nested():
                self._session.add(
                    SideEffectLedger(idempotency_key=key, tool=tool, action_id=action_id)
                )
                await self._session.flush()
        except IntegrityError:
            return False
        return True

    async def get_ledger(self, key: str) -> SideEffectLedger | None:
        return await self._session.get(SideEffectLedger, key)

    async def add_dead_letter(
        self, *, action_id: str, reason: str, context: dict[str, Any]
    ) -> None:
        self._session.add(DeadLetter(action_id=action_id, reason=reason, context=context))


class VerificationRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, action_id: str, checks: Sequence[Any]) -> None:
        """Persist every check for one action. Evidence belongs here, not in audit."""
        for check in checks:
            self._session.add(
                VerificationRow(
                    id=new_id(),
                    action_id=action_id,
                    verifier=check.verifier,
                    expected=_jsonable(check.expected),
                    observed=_jsonable(check.observed),
                    outcome=check.outcome,
                    observation_tier=check.observation_tier,
                    evidence_ref=check.evidence_ref,
                    latency_ms=check.latency_ms,
                )
            )

    async def list_by_action(self, action_id: str) -> list[VerificationRow]:
        result = await self._session.execute(
            select(VerificationRow).where(VerificationRow.action_id == action_id)
        )
        return list(result.scalars().all())


def _jsonable(value: Any) -> Any:
    """JSONB rejects non-JSON types; None stays None."""
    if value is None or isinstance(value, str | int | float | bool | list | dict):
        return value
    return str(value)


class StepRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_task(self, task_id: str) -> list[Step]:
        result = await self._session.execute(
            select(Step).where(Step.task_id == task_id).order_by(Step.ordinal)
        )
        return list(result.scalars().all())

    async def set_status(self, step_id: str, status: StepStatus) -> None:
        await self._session.execute(update(Step).where(Step.id == step_id).values(status=status))


class TaskRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, task_id: str) -> Task | None:
        return await self._session.get(Task, task_id)

    async def cas_status(
        self,
        task_id: str,
        *,
        expected: TaskStatus,
        new: TaskStatus,
        **fields: Any,
    ) -> Task | None:
        result = await self._session.execute(
            update(Task)
            .where(Task.id == task_id, Task.status == expected)
            .values(status=new, **fields)
            .returning(Task)
        )
        return result.scalar_one_or_none()

    async def runnable(self) -> list[Task]:
        """Tasks a scheduler tick must look at.

        ``WAITING_FOR_USER`` is included even though nothing can be dispatched
        for it: the tick is also what notices that the approval it was waiting
        on has been refused or has expired, and drives the task to a terminal
        state instead of leaving it parked.
        """
        result = await self._session.execute(
            select(Task).where(
                Task.status.in_((TaskStatus.READY, TaskStatus.RUNNING, TaskStatus.WAITING_FOR_USER))
            )
        )
        return list(result.scalars().all())
