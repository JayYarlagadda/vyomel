"""Plan override bypasses the planner (FR-107)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from tests.fakes import registry_with_fakes

from astra.core.plan_spec import ActionSpec, HandwrittenPlan, StepSpec
from astra.core.types import Capability, TaskOrigin
from astra.orchestrator.planning import create_task
from astra.orchestrator.tasks import TaskBounds


@pytest.mark.asyncio
@pytest.mark.req("FR-107")
async def test_plan_override_skips_decompose(settings) -> None:
    plan = HandwrittenPlan(
        steps=[
            StepSpec(
                alias="s",
                title="S",
                intent="i",
                actions=[ActionSpec(alias="a", tool="task.report", parameters={"summary": "ok"})],
            )
        ]
    )
    registry = registry_with_fakes()
    with patch("astra.orchestrator.planning.decompose", new_callable=AsyncMock) as mocked:
        from astra.store.db import dispose_engine, init_engine, session_scope

        init_engine(settings)
        try:
            async with session_scope() as session:
                task = await create_task(
                    session,
                    settings,
                    registry,
                    instruction="ignored",
                    origin=TaskOrigin.API,
                    capability_ceiling=Capability.L2,
                    context_hints={},
                    bounds=TaskBounds.from_settings(settings),
                    plan_override=plan,
                )
            mocked.assert_not_called()
            assert task.plan_version == 1
        finally:
            await dispose_engine()
