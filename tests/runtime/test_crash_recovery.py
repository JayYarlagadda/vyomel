"""Crash mid-action does not duplicate effects (FR-202, NFR-03, FR-207)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from vyomel.core.clock import FrozenClock
from vyomel.core.config import Settings
from vyomel.core.types import ActionStatus
from vyomel.orchestrator.plans import ActionSpec, HandwrittenPlan, StepSpec
from vyomel.runtime.reaper import Reaper
from vyomel.runtime.scheduler import Scheduler
from vyomel.runtime.worker import Worker
from vyomel.store.db import session_scope
from vyomel.store.models import Action
from tests.runtime.helpers import drain, install_plan


@pytest.mark.integration
@pytest.mark.req("FR-202")
@pytest.mark.req("FR-207")
async def test_expired_lease_replay_does_not_duplicate_a_read(
    runtime_db: Settings, tmp_path: Path, scheduler: Scheduler, worker: Worker
) -> None:
    """Simulate kill -9: action is RUNNING with a dead lease, then recovered.

    fs.read_file is idempotent, so replay is safe. The test asserts the action
    reaches SUCCEEDED exactly once after recovery — the crash-recovery shape
    of M1. Non-idempotent duplication is covered by the ledger reservation
    in the worker; M3 adds a mutating tool that exercises it against a file.
    """
    target = tmp_path / "once.txt"
    target.write_text("payload", encoding="utf-8")
    plan = HandwrittenPlan(
        steps=[
            StepSpec(
                alias="r",
                title="Read",
                intent="read",
                actions=[
                    ActionSpec(
                        alias="rd",
                        tool="fs.read_file",
                        parameters={"path": str(target)},
                    )
                ],
            )
        ]
    )
    task = await install_plan(runtime_db, plan)
    clock = FrozenClock()

    await scheduler.tick()  # PLANNED → READY → DISPATCHED (+ publish)

    async with session_scope() as session:
        action = (
            await session.execute(select(Action).where(Action.task_id == task.id))
        ).scalar_one()
        # Pretend a worker claimed it and died before writing a result.
        action.status = ActionStatus.RUNNING
        action.lease_owner = "ghost"
        action.lease_until = clock.now() - timedelta(seconds=1)
        action.attempt_count = 1
        action_id = action.id

    await Reaper(clock).reap(now=clock.now())
    await drain(scheduler, worker)

    async with session_scope() as session:
        action = await session.get(Action, action_id)
        assert action is not None
        assert action.status is ActionStatus.SUCCEEDED
        assert action.result is not None
        assert action.result["content"] == "payload"
        twins = list(
            (
                await session.execute(
                    select(Action).where(Action.idempotency_key == action.idempotency_key)
                )
            ).scalars()
        )
        assert len(twins) == 1
