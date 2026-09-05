"""Mock web tools."""

from __future__ import annotations

from vyomel.tools.web import WebFetchMock, WebFetchMockInput


async def test_fetch_mock_is_deterministic(tmp_path) -> None:
    from datetime import UTC, datetime, timedelta

    from vyomel.core.cancel import CancellationToken
    from vyomel.core.clock import SystemClock
    from vyomel.core.types import Capability
    from vyomel.tools.base import ToolContext

    tool = WebFetchMock()
    ctx = ToolContext(
        task_id="t",
        action_id="a",
        capability_granted=Capability.L0,
        scratch_dir=tmp_path,
        allowed_roots=[tmp_path],
        deadline=datetime.now(UTC) + timedelta(seconds=30),
        cancel=CancellationToken(),
        clock=SystemClock(),
    )
    params = WebFetchMockInput(url="https://mock.vyomel/research/001")
    first = await tool.execute(params, ctx)
    second = await tool.execute(params, ctx)
    assert first.body == second.body
    assert first.title == second.title
