"""Chaos helpers for durability evals and demos (M6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select

from vyomel.core.types import ActionStatus, TaskStatus
from vyomel.store.db import session_scope
from vyomel.store.models import Action, SideEffectLedger, Task


@dataclass(frozen=True, slots=True)
class ResearchAudit:
    task_id: str
    task_status: TaskStatus
    fetch_succeeded: int
    fetch_expected: int
    duplicate_idempotency_keys: int
    ledger_rows: int
    lost_fetches: int
    duplicate_fetches: int

    @property
    def ok(self) -> bool:
        return (
            self.task_status is TaskStatus.SUCCEEDED
            and self.lost_fetches == 0
            and self.duplicate_fetches == 0
            and self.duplicate_idempotency_keys == 0
        )


async def abandon_running_action(task_id: str) -> str | None:
    """Simulate ``kill -9`` between claim and result: RUNNING with an expired lease."""
    async with session_scope() as session:
        action = (
            await session.execute(
                select(Action)
                .where(
                    Action.task_id == task_id,
                    Action.status.in_((ActionStatus.DISPATCHED, ActionStatus.RUNNING)),
                )
                .order_by(Action.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if action is None:
            return None
        action.status = ActionStatus.RUNNING
        action.lease_owner = "worker-that-died"
        action.lease_until = action.created_at - timedelta(seconds=1)
        if action.attempt_count < 1:
            action.attempt_count = 1
        return action.id


async def audit_research_task(task_id: str, *, expected_fetches: int) -> ResearchAudit:
    async with session_scope() as session:
        task = await session.get(Task, task_id)
        if task is None:
            raise ValueError(f"unknown task {task_id}")
        actions = list(
            (await session.execute(select(Action).where(Action.task_id == task_id))).scalars()
        )
        fetches = [
            action
            for action in actions
            if action.tool == "web.fetch_mock" and action.status is ActionStatus.SUCCEEDED
        ]
        keys = [action.idempotency_key for action in fetches]
        duplicate_keys = len(keys) - len(set(keys))
        ledger_rows = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(SideEffectLedger)
                    .where(SideEffectLedger.action_id.in_([a.id for a in actions]))
                )
            ).scalar_one()
        )
        lost = max(0, expected_fetches - len(fetches))
        duplicate_fetches = max(0, len(fetches) - expected_fetches)
        return ResearchAudit(
            task_id=task_id,
            task_status=task.status,
            fetch_succeeded=len(fetches),
            fetch_expected=expected_fetches,
            duplicate_idempotency_keys=duplicate_keys,
            ledger_rows=ledger_rows,
            lost_fetches=lost,
            duplicate_fetches=duplicate_fetches,
        )
