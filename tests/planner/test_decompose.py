"""Mock planner decomposition."""

from __future__ import annotations

import pytest
from tests.fakes import registry_with_fakes

from vyomel.core.types import Capability
from vyomel.orchestrator.tools import ToolCatalog
from vyomel.planner.decompose import decompose


@pytest.mark.asyncio
async def test_mock_planner_lists_directory(settings) -> None:
    registry = registry_with_fakes()
    catalog = ToolCatalog(registry).list()
    result = await decompose(
        "list D:/tmp/workspace",
        catalog=catalog,
        capability_ceiling=Capability.L2,
        settings=settings,
        registry=registry,
    )
    assert result.plan.steps[0].actions[0].tool == "fs.list_dir"
    assert result.plan.steps[0].actions[0].parameters["path"] == "D:/tmp/workspace"


@pytest.mark.asyncio
async def test_mock_planner_falls_back_to_task_report(settings) -> None:
    registry = registry_with_fakes()
    catalog = ToolCatalog(registry).list()
    result = await decompose(
        "summarize the quarterly review",
        catalog=catalog,
        capability_ceiling=Capability.L2,
        settings=settings,
        registry=registry,
    )
    assert result.plan.steps[0].actions[0].tool == "task.report"
