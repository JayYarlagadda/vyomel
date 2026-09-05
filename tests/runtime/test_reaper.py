"""Lease reaper returns abandoned work to READY (FR-210)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from tests.runtime.helpers import install_plan
from vyomel.core.clock import FrozenClock
from vyomel.core.config import Settings
from vyomel.core.types import ActionStatus
from vyomel.orchestrator.plans import ActionSpec, HandwrittenPlan, StepSpec
from vyomel.runtime.reaper import Reaper
from vyomel.store.db import session_scope
from vyomel.store.models import Action


@pytest.mark.integration
@pytest.mark.req("FR-210")
async def test_reaper_returns_expired_running_action_to_ready(
    runtime_db: Settings, tmp_path: Path
) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
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
                        parameters={"path": str(tmp_path / "a.txt")},
                    )
                ],
            )
        ]
    )
    task = await install_plan(runtime_db, plan)
    clock = FrozenClock()

    async with session_scope() as session:
        action = (
            await session.execute(select(Action).where(Action.task_id == task.id))
        ).scalar_one()
        action.status = ActionStatus.RUNNING
        action.lease_owner = "dead-worker"
        action.lease_until = clock.now() - timedelta(seconds=1)
        action.attempt_count = 1
        action_id = action.id

    reclaimed = await Reaper(clock).reap(now=clock.now())
    assert action_id in reclaimed

    async with session_scope() as session:
        action = await session.get(Action, action_id)
        assert action is not None
        assert action.status is ActionStatus.READY
        assert action.lease_owner is None
        assert action.lease_until is None
