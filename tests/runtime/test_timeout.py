"""Action timeouts (FR-205).

The worker wraps every tool call in ``asyncio.wait_for``. What matters is not
that the coroutine is cancelled but what the *row* looks like afterwards: a
timeout must be a retryable failure that releases the lease, and the last
timeout must dead-letter rather than retry forever.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, update

from tests.runtime.helpers import install_plan
from vyomel.core.config import Settings
from vyomel.core.errors import ErrorCode
from vyomel.core.types import ActionStatus
from vyomel.orchestrator.plans import ActionSpec, HandwrittenPlan, StepSpec
from vyomel.runtime.scheduler import Scheduler
from vyomel.runtime.worker import Worker
from vyomel.store.db import session_scope
from vyomel.store.models import Action, DeadLetter

_PLAN = HandwrittenPlan(
    steps=[
        StepSpec(
            alias="slow",
            title="Sleep past the deadline",
            intent="exercise the timeout path",
            # test.sleep declares a 1s timeout; 10s guarantees it is exceeded.
            actions=[ActionSpec(alias="zzz", tool="test.sleep", parameters={"seconds": 10.0})],
        )
    ]
)


async def _only_action(task_id: str) -> Action:
    async with session_scope() as session:
        return (await session.execute(select(Action).where(Action.task_id == task_id))).scalar_one()


@pytest.mark.integration
@pytest.mark.req("FR-205")
async def test_a_slow_action_times_out_and_is_released_for_retry(
    runtime_db: Settings, scheduler: Scheduler, worker: Worker
) -> None:
    task = await install_plan(runtime_db, _PLAN, instruction="sleep too long")
    action = await _only_action(task.id)
    assert action.timeout_s == 1  # min(tool default, settings.action_timeout_s)

    await scheduler.tick()
    assert await worker.run_once(block_ms=50)

    action = await _only_action(task.id)
    assert action.status is ActionStatus.READY
    assert action.attempt_count == 1
    assert action.error is not None
    assert action.error["code"] == ErrorCode.TIMEOUT.value
    assert action.error["retryable"] is True
    # Lease released and a backoff gate set, so the reaper leaves it alone and
    # the dispatcher will not re-publish it in the same instant.
    assert action.lease_owner is None
    assert action.lease_until is None
    assert action.available_at is not None


@pytest.mark.integration
@pytest.mark.req("FR-205")
async def test_timeout_with_no_retries_left_fails_and_dead_letters(
    runtime_db: Settings, scheduler: Scheduler, worker: Worker
) -> None:
    task = await install_plan(runtime_db, _PLAN, instruction="sleep too long, once")
    async with session_scope() as session:
        await session.execute(update(Action).where(Action.task_id == task.id).values(max_retries=0))

    await scheduler.tick()
    assert await worker.run_once(block_ms=50)

    action = await _only_action(task.id)
    assert action.status is ActionStatus.FAILED
    assert action.finished_at is not None
    async with session_scope() as session:
        dead = (
            await session.execute(select(DeadLetter).where(DeadLetter.action_id == action.id))
        ).scalar_one()
    assert dead.reason == ErrorCode.TIMEOUT.value
