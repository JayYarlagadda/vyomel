"""Model provider protocol (FR-701)."""

from __future__ import annotations

import pytest

from astra.models.providers.mock import MockPlannerProvider
from astra.models.types import ChatMessage, ModelRequest


@pytest.mark.asyncio
@pytest.mark.req("FR-701")
async def test_mock_provider_returns_structured_plan() -> None:
    provider = MockPlannerProvider()
    response = await provider.complete(
        ModelRequest(
            purpose="planner.decompose",
            messages=(ChatMessage(role="user", content="User instruction:\nlist D:/tmp"),),
        )
    )
    assert response.parsed is not None
    assert response.parsed["steps"][0]["actions"][0]["tool"] == "fs.list_dir"
