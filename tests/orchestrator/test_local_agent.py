"""Local-agent hub routing (ADR-0009 / M13)."""

from __future__ import annotations

import pytest

from vyomel.core.types import Capability
from vyomel.orchestrator.local_agent import LocalAgentHub, reset_local_agent_hub


@pytest.mark.asyncio
async def test_hub_registers_and_dispatches_desktop_tool() -> None:
    hub = LocalAgentHub()
    reset_local_agent_hub(hub)
    session = await hub.register(
        "laptop",
        tools=["desktop.click_element", "desktop.set_field"],
        ceiling=Capability.L2,
    )
    assert hub.has_handler_for("desktop.click_element")
    assert not hub.has_handler_for("fs.read_file")

    ok = await hub.try_dispatch(
        "act_1",
        tool="desktop.click_element",
        parameters={"ref": "btn"},
        capability_level=Capability.L1,
    )
    assert ok is True
    job = await session.queue.get()
    assert job["action_id"] == "act_1"
    assert job["tool"] == "desktop.click_element"


@pytest.mark.asyncio
async def test_hub_rejects_above_ceiling() -> None:
    hub = LocalAgentHub()
    await hub.register("laptop", tools=["desktop.click_element"], ceiling=Capability.L1)
    ok = await hub.try_dispatch(
        "act_2",
        tool="desktop.click_element",
        parameters={},
        capability_level=Capability.L3,
    )
    assert ok is False


@pytest.mark.asyncio
async def test_hub_returns_false_when_no_agent() -> None:
    hub = LocalAgentHub()
    ok = await hub.try_dispatch(
        "act_3",
        tool="desktop.click_element",
        parameters={},
        capability_level=Capability.L1,
    )
    assert ok is False
