"""Persist steps and actions before dispatch (FR-201)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from tests.runtime.helpers import install_plan
from vyomel.core.config import Settings
from vyomel.core.types import ActionStatus, TaskStatus
from vyomel.orchestrator.plans import ActionSpec, HandwrittenPlan, StepSpec
from vyomel.store.db import session_scope
from vyomel.store.models import Action, Step, Task


def _two_step_plan(root: Path) -> HandwrittenPlan:
    (root / "a.txt").write_text("x", encoding="utf-8")
    return HandwrittenPlan(
        steps=[
            StepSpec(
                alias="list",
                title="List",
                intent="See the workspace",
                actions=[
                    ActionSpec(
                        alias="ls",
                        tool="fs.list_dir",
                        parameters={"path": str(root)},
                    )
                ],
            ),
            StepSpec(
                alias="read",
                title="Read",
                intent="Read a file",
                depends_on=["list"],
                actions=[
                    ActionSpec(
                        alias="rd",
                        tool="fs.read_file",
                        parameters={"path": str(root / "a.txt")},
                    )
                ],
            ),
        ]
    )


@pytest.mark.integration
@pytest.mark.req("FR-201")
async def test_handwritten_plan_is_persisted_before_any_dispatch(
    runtime_db: Settings, tmp_path: Path
) -> None:
    task = await install_plan(runtime_db, _two_step_plan(tmp_path), instruction="list then read")

    async with session_scope() as session:
        stored = await session.get(Task, task.id)
        assert stored is not None
        assert stored.status is TaskStatus.READY
        steps = list((await session.execute(select(Step).where(Step.task_id == task.id))).scalars())
        actions = list(
            (await session.execute(select(Action).where(Action.task_id == task.id))).scalars()
        )

    assert len(steps) == 2
    assert len(actions) == 2
    assert {a.status for a in actions} == {ActionStatus.PLANNED}
    assert all(len(a.idempotency_key) == 64 for a in actions)
