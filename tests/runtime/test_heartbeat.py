"""Lease heartbeat while a tool is executing (FR-208, docs/07 §4.4)."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from astra.core.clock import FrozenClock
from astra.core.config import Settings
from astra.core.types import ActionStatus
from astra.orchestrator.plans import ActionSpec, HandwrittenPlan, StepSpec
from astra.orchestrator.runtime import make_scheduler, make_worker
from astra.runtime.queue import ActionQueue
from astra.runtime.reaper import Reaper
from astra.runtime.worker import Worker
from astra.store.db import session_scope
from astra.store.models import Action
from astra.store.repos import ActionRepo
from tests.fakes import release_hold, reset_hold, signal_hold_started
from tests.runtime.helpers import install_plan


def _hold_plan(*, timeout_s: int = 5) -> HandwrittenPlan:
    return HandwrittenPlan(
        steps=[
            StepSpec(
                alias="hold",
                title="Hold",
                intent="block until released",
                actions=[
                    ActionSpec(
                        alias="h",
                        tool="test.hold",
                        parameters={},
                        timeout_s=timeout_s,
                    )
                ],
            )
        ]
    )


@pytest.mark.integration
@pytest.mark.req("FR-208")
async def test_extend_lease_requires_matching_worker(runtime_db: Settings) -> None:
    task = await install_plan(runtime_db, _hold_plan())
    async with session_scope() as session:
        action = (
            await session.execute(select(Action).where(Action.task_id == task.id))
        ).scalar_one()
        action.status = ActionStatus.RUNNING
        action.lease_owner = "owner-a"
        action.lease_until = action.created_at
        action_id = action.id

    clock = FrozenClock()
    async with session_scope() as session:
        repo = ActionRepo(session)
        assert (
            await repo.extend_lease(
                action_id,
                worker_id="owner-b",
                lease_until=clock.now() + timedelta(seconds=5),
            )
        ) is None
        extended = await repo.extend_lease(
            action_id,
            worker_id="owner-a",
            lease_until=clock.now() + timedelta(seconds=5),
        )
        assert extended is not None
        assert extended.lease_owner == "owner-a"


@pytest.mark.integration
@pytest.mark.req("FR-208")
async def test_heartbeat_extends_lease_past_reaper_window(
    runtime_db: Settings,
    queue: ActionQueue,
) -> None:
    """A slow action survives reaper passes when the worker heartbeats."""
    settings = runtime_db.model_copy(update={"heartbeat_interval_s": 0.05})
    clock = FrozenClock()
    scheduler = make_scheduler(settings, queue)
    worker = make_worker(settings, queue, worker_id="hb-worker", clock=clock)

    reset_hold()
    task = await install_plan(settings, _hold_plan(timeout_s=5))
    await scheduler.tick()

    run_task = asyncio.create_task(worker.run_once(block_ms=50))
    await asyncio.wait_for(signal_hold_started().wait(), timeout=2.0)

    async with session_scope() as session:
        action = (
            await session.execute(select(Action).where(Action.task_id == task.id))
        ).scalar_one()
        action_id = action.id
        assert action.status is ActionStatus.RUNNING

    clock.advance(10.0)
    await asyncio.sleep(0.15)  # real time for heartbeat loop to extend the lease

    reclaimed = await Reaper(clock).reap(now=clock.now())
    assert action_id not in reclaimed

    release_hold()
    assert await asyncio.wait_for(run_task, timeout=2.0)

    async with session_scope() as session:
        action = await session.get(Action, action_id)
        assert action is not None
        assert action.status is ActionStatus.SUCCEEDED


@pytest.mark.integration
@pytest.mark.req("FR-208")
async def test_expired_lease_is_reaped_when_heartbeat_disabled(
    runtime_db: Settings,
    queue: ActionQueue,
) -> None:
    settings = runtime_db.model_copy(update={"heartbeat_interval_s": 0})
    clock = FrozenClock()
    scheduler = make_scheduler(settings, queue)
    worker: Worker = make_worker(settings, queue, worker_id="no-hb", clock=clock)

    reset_hold()
    task = await install_plan(settings, _hold_plan(timeout_s=5))
    await scheduler.tick()

    run_task = asyncio.create_task(worker.run_once(block_ms=50))
    await asyncio.wait_for(signal_hold_started().wait(), timeout=2.0)

    async with session_scope() as session:
        action = (
            await session.execute(select(Action).where(Action.task_id == task.id))
        ).scalar_one()
        action_id = action.id

    clock.advance(10.0)
    reclaimed = await Reaper(clock).reap(now=clock.now())
    assert action_id in reclaimed

    release_hold()
    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task
