"""Pause/resume/cancel lifecycle (FR-209).

Cancel must compensate reversible SUCCEEDED actions in reverse topological
order. The order is load-bearing: undoing a create before an overwrite of the
same file would restore the overwrite's backup onto a path that no longer
exists, then leave the file behind.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from tests.runtime.helpers import install_plan
from vyomel.core.config import Settings
from vyomel.core.types import ActionStatus, TaskStatus
from vyomel.orchestrator.plans import ActionSpec, HandwrittenPlan, StepSpec
from vyomel.orchestrator.runtime import get_registry
from vyomel.runtime.cancel import Canceller
from vyomel.runtime.scheduler import Scheduler
from vyomel.runtime.worker import Worker
from vyomel.store.db import session_scope
from vyomel.store.models import Action


def _scratch_writes(scratch: Path) -> tuple[HandwrittenPlan, Path]:
    target = scratch / "notes.txt"
    plan = HandwrittenPlan(
        steps=[
            StepSpec(
                alias="create",
                title="Create",
                intent="Write the first value",
                actions=[
                    ActionSpec(
                        alias="write1",
                        tool="fs.write_file",
                        parameters={"path": str(target), "content": "1"},
                    )
                ],
            ),
            StepSpec(
                alias="overwrite",
                title="Overwrite",
                intent="Write the second value",
                depends_on=["create"],
                actions=[
                    ActionSpec(
                        alias="write2",
                        tool="fs.write_file",
                        parameters={"path": str(target), "content": "2"},
                    )
                ],
            ),
        ]
    )
    return plan, target


@pytest.mark.integration
@pytest.mark.req("FR-209")
async def test_cancel_compensates_in_reverse_topo_order(
    runtime_db: Settings,
    scheduler: Scheduler,
    worker: Worker,
) -> None:
    scratch = runtime_db.scratch_dir
    scratch.mkdir(parents=True, exist_ok=True)
    plan, target = _scratch_writes(scratch)
    task = await install_plan(runtime_db, plan, instruction="write then overwrite")

    for _ in range(40):
        async with session_scope() as session:
            actions = list(
                (await session.execute(select(Action).where(Action.task_id == task.id))).scalars()
            )
        if actions and all(a.status is ActionStatus.SUCCEEDED for a in actions):
            break
        await scheduler.tick()
        await worker.run_once(block_ms=50)
    else:
        raise AssertionError("writes did not both succeed")

    async with session_scope() as session:
        actions = list(
            (await session.execute(select(Action).where(Action.task_id == task.id))).scalars()
        )
    assert {a.status for a in actions} == {ActionStatus.SUCCEEDED}
    assert target.read_text(encoding="utf-8") == "2"
    by_alias_tool = {a.parameters["content"]: a.id for a in actions}

    async with session_scope() as session:
        report = await Canceller(runtime_db, get_registry()).cancel(
            session, task.id, compensate=True, actor="test"
        )

    assert report.status is TaskStatus.CANCELLED
    assert report.irreversible == []
    assert report.failed == []
    assert report.compensated == [by_alias_tool["2"], by_alias_tool["1"]]
    assert not target.exists()

    async with session_scope() as session:
        settled = list(
            (await session.execute(select(Action).where(Action.task_id == task.id))).scalars()
        )
    assert {a.status for a in settled} == {ActionStatus.ROLLED_BACK}


@pytest.mark.integration
@pytest.mark.req("FR-209")
async def test_cancel_reports_irreversible_effects_and_stops_the_rest(
    runtime_db: Settings,
    scheduler: Scheduler,
    worker: Worker,
) -> None:
    scratch = runtime_db.scratch_dir
    scratch.mkdir(parents=True, exist_ok=True)
    later = scratch / "later.txt"
    plan = HandwrittenPlan(
        steps=[
            StepSpec(
                alias="observe",
                title="Observe",
                intent="Read-only shell",
                actions=[
                    ActionSpec(
                        alias="who",
                        tool="shell.run",
                        parameters={"argv": ["whoami"]},
                    )
                ],
            ),
            StepSpec(
                alias="write",
                title="Write",
                intent="A reversible write that should not run",
                depends_on=["observe"],
                actions=[
                    ActionSpec(
                        alias="out",
                        tool="fs.write_file",
                        parameters={"path": str(later), "content": "nope"},
                    )
                ],
            ),
        ]
    )
    task = await install_plan(runtime_db, plan, instruction="whoami then write")

    # Run until the first action has succeeded, then cancel before another tick
    # can dispatch the write.
    for _ in range(40):
        async with session_scope() as session:
            actions = list(
                (await session.execute(select(Action).where(Action.task_id == task.id))).scalars()
            )
        who = next(a for a in actions if a.tool == "shell.run")
        if who.status is ActionStatus.SUCCEEDED:
            break
        await scheduler.tick()
        await worker.run_once(block_ms=50)
    else:
        raise AssertionError("shell.run did not succeed")

    async with session_scope() as session:
        report = await Canceller(runtime_db, get_registry()).cancel(
            session, task.id, compensate=True, actor="test"
        )

    assert report.status is TaskStatus.CANCELLED
    assert any(item.tool == "shell.run" for item in report.irreversible)
    assert not later.exists()
    async with session_scope() as session:
        settled = list(
            (await session.execute(select(Action).where(Action.task_id == task.id))).scalars()
        )
    write = next(a for a in settled if a.tool == "fs.write_file")
    assert write.status is ActionStatus.CANCELLED
