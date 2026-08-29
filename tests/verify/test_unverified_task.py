"""UNVERIFIED without an opt-in must not make the task SUCCEEDED."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from tests.runtime.helpers import drain, install_plan

from astra.core.config import Settings
from astra.core.types import ActionStatus, Capability, TaskStatus
from astra.orchestrator.plans import ActionSpec, HandwrittenPlan, StepSpec
from astra.runtime.scheduler import Scheduler
from astra.runtime.worker import Worker
from astra.store.db import session_scope
from astra.store.models import Action, Task


def _opaque_plan(*, tolerate: bool) -> HandwrittenPlan:
    return HandwrittenPlan(
        steps=[
            StepSpec(
                alias="do",
                title="Something unverifiable",
                intent="Do a thing we cannot re-observe",
                tolerates_unverified=tolerate,
                actions=[ActionSpec(alias="o", tool="test.opaque", parameters={"note": "x"})],
            )
        ]
    )


@pytest.mark.integration
@pytest.mark.req("FR-402")
async def test_unverified_without_opt_in_fails_the_task(
    runtime_db: Settings, scheduler: Scheduler, worker: Worker
) -> None:
    task = await install_plan(
        runtime_db, _opaque_plan(tolerate=False), instruction="opaque", ceiling=Capability.L1
    )
    await drain(scheduler, worker)

    async with session_scope() as session:
        stored = await session.get(Task, task.id)
        action = (
            await session.execute(select(Action).where(Action.task_id == task.id))
        ).scalar_one()

    assert action.status is ActionStatus.UNVERIFIED
    assert stored is not None
    assert stored.status is TaskStatus.FAILED
    assert stored.error is not None
    assert stored.error["code"] == "UNVERIFIED"


@pytest.mark.integration
@pytest.mark.req("FR-402")
async def test_unverified_is_success_only_when_the_step_opts_in(
    runtime_db: Settings, scheduler: Scheduler, worker: Worker
) -> None:
    task = await install_plan(
        runtime_db, _opaque_plan(tolerate=True), instruction="opaque-ok", ceiling=Capability.L1
    )
    await drain(scheduler, worker)

    async with session_scope() as session:
        stored = await session.get(Task, task.id)
        action = (
            await session.execute(select(Action).where(Action.task_id == task.id))
        ).scalar_one()

    assert action.status is ActionStatus.UNVERIFIED
    assert stored is not None
    assert stored.status is TaskStatus.SUCCEEDED
