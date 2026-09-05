"""Offline / local-only model path (NFR-12)."""

from __future__ import annotations

import pytest

from vyomel.core.config import Settings
from vyomel.core.errors import PrivacyRoutingViolation
from vyomel.core.types import Sensitivity
from vyomel.models.router import get_planner_provider
from vyomel.models.types import ChatMessage, ModelRequest


@pytest.mark.req("NFR-12")
def test_offline_mode_rejects_remote_backends() -> None:
    settings = Settings(env="dev", offline=True, planner_backend="openai", openai_api_key="sk-test")
    with pytest.raises(PrivacyRoutingViolation):
        get_planner_provider(settings)


@pytest.mark.req("NFR-12")
def test_offline_mode_allows_mock_local_planner() -> None:
    settings = Settings(env="test", offline=True, planner_backend="mock")
    provider = get_planner_provider(settings)
    assert provider.info.is_remote is False


@pytest.mark.asyncio
@pytest.mark.req("NFR-12")
async def test_offline_mock_completes_a_plan_without_network() -> None:
    settings = Settings(env="test", offline=True, planner_backend="mock")
    provider = get_planner_provider(settings)
    response = await provider.complete(
        ModelRequest(
            purpose="planner.decompose",
            messages=(ChatMessage(role="user", content="User instruction:\nlist D:/tmp"),),
            sensitivity=Sensitivity.SENSITIVE,
        )
    )
    assert response.parsed is not None
    assert response.provider == "mock-planner"
