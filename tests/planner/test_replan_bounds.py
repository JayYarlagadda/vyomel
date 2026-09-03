"""Bounded replanning (FR-106)."""

from __future__ import annotations

import pytest
from tests.runtime.helpers import drain, install_plan

from astra.core.plan_spec import ActionSpec, HandwrittenPlan, StepSpec
from astra.core.types import TaskStatus
from astra.runtime.scheduler import Scheduler
from astra.runtime.worker import Worker
from astra.store.models import Task


def _fail_plan() -> HandwrittenPlan:
    return HandwrittenPlan(
        steps=[
            StepSpec(
                alias="fail_step",
                title="Fail",
                intent="trigger replan",
                actions=[
                    ActionSpec(
                        alias="boom",
                        tool="test.fail_hard",
                        parameters={"reason": "intentional"},
                        max_retries=0,
                    )
                ],
            )
        ]
    )


@pytest.mark.integration
@pytest.mark.req("FR-106")
async def test_replan_recovers_from_failure(
    runtime_db, scheduler: Scheduler, worker: Worker
) -> None:
    task = await install_plan(runtime_db, _fail_plan(), instruction="fail then recover")
    for _ in range(100):
        await scheduler.tick()
        await worker.run_once(block_ms=50)
    from astra.store.db import session_scope

    async with session_scope() as session:
        refreshed = await session.get(Task, task.id)
        assert refreshed is not None
        assert refreshed.status is TaskStatus.SUCCEEDED
        assert refreshed.replan_count >= 1


@pytest.mark.integration
@pytest.mark.req("FR-106")
async def test_replan_exhaustion_enters_needs_human(
    runtime_db, scheduler: Scheduler, worker: Worker
) -> None:
    runtime_db.max_replans = 0
    task = await install_plan(runtime_db, _fail_plan())
    await drain(scheduler, worker, rounds=80)
    from astra.store.db import session_scope

    async with session_scope() as session:
        refreshed = await session.get(Task, task.id)
        assert refreshed is not None
        assert refreshed.status is TaskStatus.NEEDS_HUMAN
