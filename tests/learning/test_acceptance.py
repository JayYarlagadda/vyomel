"""Explicit acceptance before learned workflows are invocable (FR-903)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vyomel.core.cancel import CancellationToken
from vyomel.core.clock import FrozenClock
from vyomel.core.config import Settings
from vyomel.core.errors import ErrorCode, ToolError
from vyomel.core.types import Capability
from vyomel.learning.service import mine_and_propose
from vyomel.learning.signatures import ObservedAction
from vyomel.learning.store import (
    accept_workflow,
    expand_workflow,
    get_workflow_store,
    reject_workflow,
    reset_workflow_store,
)
from vyomel.tools.base import ToolContext
from vyomel.tools.registry import default_registry

NOW = datetime(2026, 9, 4, 23, 0, tzinfo=UTC)


def _actions(n: int = 3) -> list[ObservedAction]:
    out: list[ObservedAction] = []
    for i in range(n):
        root = f"/workspace/p{i}"
        tid = f"task{i}"
        out.extend(
            [
                ObservedAction(tool="fs.list_dir", parameters={"path": root}, task_id=tid),
                ObservedAction(
                    tool="fs.read_file",
                    parameters={"path": f"{root}/in.md"},
                    task_id=tid,
                ),
                ObservedAction(
                    tool="fs.write_file",
                    parameters={"path": f"{root}/out.md", "content": "ok"},
                    task_id=tid,
                ),
            ]
        )
    return out


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_workflow_store()


@pytest.mark.req("FR-903")
def test_unaccepted_workflow_cannot_be_expanded() -> None:
    proposals = mine_and_propose(_actions())
    proposal = proposals[0]
    assert proposal.status == "proposed"
    with pytest.raises(Exception) as exc:
        expand_workflow(get_workflow_store(), proposal.id, {})
    assert "not accepted" in str(exc.value).lower()


@pytest.mark.req("FR-903")
def test_accept_then_invoke() -> None:
    proposals = mine_and_propose(_actions())
    proposal = proposals[0]
    accepted = accept_workflow(get_workflow_store(), proposal.id)
    assert accepted.status == "accepted"
    values = {p.name: f"v-{p.name}" for p in accepted.parameters}
    steps = expand_workflow(get_workflow_store(), accepted.id, values)
    assert len(steps) == len(accepted.definition)


@pytest.mark.req("FR-903")
def test_reject_suppresses_reproposal() -> None:
    store = get_workflow_store()
    proposals = mine_and_propose(_actions(), store=store)
    proposal = proposals[0]
    reject_workflow(store, proposal.id)
    again = mine_and_propose(_actions(), store=store)
    assert all(p.pattern_key != proposal.pattern_key for p in again)


@pytest.mark.req("FR-903")
async def test_workflow_invoke_tool_requires_acceptance(tmp_path: Path) -> None:
    proposals = mine_and_propose(_actions())
    proposal = proposals[0]
    settings = Settings(
        env="test",
        workspace_root=tmp_path / ".vyomel",
        allowed_roots=[tmp_path],
        workflow_store_backend="memory",
    )
    settings.ensure_directories()
    ctx = ToolContext(
        task_id="wf",
        action_id="a1",
        capability_granted=Capability.L2,
        scratch_dir=settings.scratch_dir,
        allowed_roots=[tmp_path],
        deadline=NOW + timedelta(hours=1),
        cancel=CancellationToken(),
        clock=FrozenClock(NOW),
        trash_dir=settings.trash_dir,
        settings=settings,
    )
    tool = default_registry().get("workflow.invoke")
    with pytest.raises(ToolError) as exc:
        await tool.execute(tool.Input(workflow_id=proposal.id, parameters={}), ctx)
    assert exc.value.code is ErrorCode.PERMISSION_DENIED

    accept_workflow(get_workflow_store(), proposal.id)
    values = {p.name: "x" for p in proposal.parameters}
    result = await tool.execute(
        tool.Input(workflow_id=proposal.id, parameters=values),
        ctx,
    )
    assert result.workflow_id == proposal.id  # type: ignore[attr-defined]
    assert result.steps  # type: ignore[attr-defined]
