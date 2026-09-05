"""Episodic memory after successful tasks (FR-507)."""

from __future__ import annotations

import pytest
from tests.runtime.helpers import drain, install_plan

from vyomel.core.types import TaskStatus
from vyomel.memory.episodes import list_episodes
from vyomel.orchestrator.plans import ActionSpec, HandwrittenPlan, StepSpec
from vyomel.store.db import session_scope
from vyomel.store.repos import TaskRepo


@pytest.mark.integration
@pytest.mark.req("FR-507")
async def test_successful_task_records_episode(runtime_db, scheduler, worker) -> None:
    plan = HandwrittenPlan(
        steps=[
            StepSpec(
                alias="wrap",
                title="Report",
                intent="Finish with a summary",
                actions=[
                    ActionSpec(
                        alias="report",
                        tool="task.report",
                        parameters={"summary": "Benchmarked Orbit failover recovery."},
                    )
                ],
            )
        ]
    )
    task = await install_plan(runtime_db, plan, instruction="record episode")
    await drain(scheduler, worker)

    async with session_scope() as session:
        current = await TaskRepo(session).get(task.id)
        assert current.status is TaskStatus.SUCCEEDED
        episodes = await list_episodes(session, limit=10)
        assert any(episode.task_id == task.id for episode in episodes)
        episode = next(item for item in episodes if item.task_id == task.id)
        assert "Orbit failover" in episode.summary
