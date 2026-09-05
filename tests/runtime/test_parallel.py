"""Handwritten 5-action DAG executes end-to-end (FR-204, M1 exit)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from vyomel.core.config import Settings
from vyomel.core.types import ActionStatus, TaskStatus
from vyomel.orchestrator.plans import ActionSpec, HandwrittenPlan, StepSpec
from vyomel.runtime.scheduler import Scheduler
from vyomel.runtime.worker import Worker
from vyomel.store.db import session_scope
from vyomel.store.models import Action, Task
from tests.runtime.helpers import drain, install_plan


def _five_action_plan(root: Path) -> HandwrittenPlan:
    for name, body in (("one.md", "alpha"), ("two.md", "beta"), ("three.md", "gamma")):
        (root / name).write_text(body, encoding="utf-8")
    return HandwrittenPlan(
        steps=[
            StepSpec(
                alias="survey",
                title="Survey",
                intent="List files",
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
                title="Read three files in parallel",
                intent="Independent reads after the listing",
                depends_on=["survey"],
                actions=[
                    ActionSpec(
                        alias="r1",
                        tool="fs.read_file",
                        parameters={"path": str(root / "one.md")},
                    ),
                    ActionSpec(
                        alias="r2",
                        tool="fs.read_file",
                        parameters={"path": str(root / "two.md")},
                    ),
                    ActionSpec(
                        alias="r3",
                        tool="fs.read_file",
                        parameters={"path": str(root / "three.md")},
                    ),
                ],
            ),
            StepSpec(
                alias="wrap",
                title="Report",
                intent="Summarize",
                depends_on=["read"],
                actions=[
                    ActionSpec(
                        alias="rep",
                        tool="task.report",
                        parameters={
                            "summary": "listed and read three files",
                            "findings": ["alpha", "beta", "gamma"],
                        },
                    )
                ],
            ),
        ]
    )


@pytest.mark.integration
@pytest.mark.req("FR-204")
async def test_five_action_dag_completes(
    runtime_db: Settings, tmp_path: Path, scheduler: Scheduler, worker: Worker
) -> None:
    task = await install_plan(runtime_db, _five_action_plan(tmp_path), instruction="5-action dag")
    await drain(scheduler, worker)

    async with session_scope() as session:
        stored = await session.get(Task, task.id)
        assert stored is not None
        assert stored.status is TaskStatus.SUCCEEDED
        assert stored.result is not None
        assert stored.result["summary"] == "listed and read three files"
        actions = list(
            (await session.execute(select(Action).where(Action.task_id == task.id))).scalars()
        )

    assert len(actions) == 5
    assert {a.status for a in actions} == {ActionStatus.SUCCEEDED}
    reads = [a for a in actions if a.tool == "fs.read_file"]
    assert {a.result["content"] for a in reads if a.result} == {"alpha", "beta", "gamma"}
