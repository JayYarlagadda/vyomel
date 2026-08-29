"""Cooperative cancel of a live execute (07 §8, FR-209).

The canceller must not CAS ``RUNNING → CANCELLED``. The worker holds a
per-action token, observes the cancelled task row, and after ``cancel_grace_s``
abandons the execute so a mutation that has not been committed is not lost
and not pretended undone.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select, update

from astra.core.config import Settings
from astra.core.types import ActionStatus, TaskStatus
from astra.orchestrator.plans import ActionSpec, HandwrittenPlan, StepSpec
from astra.orchestrator.runtime import get_registry
from astra.runtime.cancel import Canceller
from astra.runtime.scheduler import Scheduler
from astra.runtime.worker import Worker
from astra.store.db import session_scope
from astra.store.models import Action
from tests.runtime.helpers import install_plan

_PLAN = HandwrittenPlan(
    steps=[
        StepSpec(
            alias="slow",
            title="Sleep",
            intent="stay RUNNING long enough to cancel",
            actions=[ActionSpec(alias="zzz", tool="test.sleep", parameters={"seconds": 8.0})],
        )
    ]
)


async def _only_action(task_id: str) -> Action:
    async with session_scope() as session:
        return (await session.execute(select(Action).where(Action.task_id == task_id))).scalar_one()


@pytest.mark.integration
@pytest.mark.req("FR-209")
async def test_a_running_action_is_cancelled_cooperatively_not_seized(
    runtime_db: Settings, scheduler: Scheduler, worker: Worker
) -> None:
    task = await install_plan(runtime_db, _PLAN, instruction="sleep then cancel")
    async with session_scope() as session:
        await session.execute(
            update(Action).where(Action.task_id == task.id).values(timeout_s=15, max_retries=0)
        )

    await scheduler.tick()
    work = asyncio.create_task(worker.run_once(block_ms=2_000))
    for _ in range(100):
        action = await _only_action(task.id)
        if action.status is ActionStatus.RUNNING:
            break
        await asyncio.sleep(0.02)
    else:
        work.cancel()
        raise AssertionError("action never entered RUNNING")

    async with session_scope() as session:
        report = await Canceller(runtime_db, get_registry()).cancel(
            session, task.id, compensate=True, actor="test"
        )

    await work
    action = await _only_action(task.id)
    assert action.status is ActionStatus.CANCELLED
    assert action.result is None
    assert report.status is TaskStatus.CANCELLED
    assert report.still_running == []
