"""Deterministic model cache (FR-706)."""

from __future__ import annotations

import pytest

from astra.models.cache import CachedProvider
from astra.models.providers.mock import MockPlannerProvider
from astra.models.types import ChatMessage, ModelRequest


@pytest.mark.asyncio
@pytest.mark.req("FR-706")
async def test_cache_returns_identical_response() -> None:
    provider = CachedProvider(MockPlannerProvider())
    req = ModelRequest(
        purpose="planner.decompose",
        messages=(ChatMessage(role="user", content="User instruction:\nlist D:/x"),),
        temperature=0.0,
        seed=0,
    )
    first = await provider.complete(req)
    second = await provider.complete(req)
    assert first.content == second.content
    assert second.cache_hit is True
