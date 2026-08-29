"""Injected wrong-value writes are caught. The M3 headline number."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from tests.runtime.helpers import drain, install_plan

from astra.core.config import Settings
from astra.core.errors import ErrorCode
from astra.core.types import ActionStatus, Capability, TaskStatus
from astra.orchestrator.plans import ActionSpec, HandwrittenPlan, StepSpec
from astra.runtime.scheduler import Scheduler
from astra.runtime.worker import Worker
from astra.store.db import session_scope
from astra.store.models import Action, Task, Verification


def _lying_plan(path: Path) -> HandwrittenPlan:
    return HandwrittenPlan(
        steps=[
            StepSpec(
                alias="write",
                title="Write a grade",
                intent="Record 87",
                actions=[
                    ActionSpec(
                        alias="w",
                        tool="test.lying_write",
                        parameters={
                            "path": str(path),
                            "claimed": "87",
                            "actual": "78",
                        },
                    )
                ],
            )
        ]
    )


@pytest.mark.integration
@pytest.mark.req("FR-401")
@pytest.mark.req("FR-402")
async def test_a_lying_write_is_caught_every_time(
    runtime_db: Settings, tmp_path: Path, scheduler: Scheduler, worker: Worker
) -> None:
    target = tmp_path / "grade.txt"
    task = await install_plan(
        runtime_db, _lying_plan(target), instruction="write 87", ceiling=Capability.L1
    )
    await drain(scheduler, worker)

    async with session_scope() as session:
        stored = await session.get(Task, task.id)
        action = (
            await session.execute(select(Action).where(Action.task_id == task.id))
        ).scalar_one()
        checks = list(
            (
                await session.execute(
                    select(Verification).where(Verification.action_id == action.id)
                )
            ).scalars()
        )

    assert stored is not None
    assert stored.status is TaskStatus.FAILED
    assert action.status is ActionStatus.FAILED
    assert action.error is not None
    assert action.error["code"] == ErrorCode.VERIFICATION_FAILED.value
    assert checks
    assert all(row.verifier == "file_hash" for row in checks)
    assert {row.outcome.value for row in checks} == {"FAIL"}
    assert target.read_text(encoding="utf-8") == "78"
