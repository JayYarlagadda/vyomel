"""vLLM provider adapter (FR-707)."""

from __future__ import annotations

import pytest

from vyomel.core.config import Settings
from vyomel.core.errors import ConfigError
from vyomel.models.providers.vllm import VllmProvider
from vyomel.models.router import build_provider, get_planner_provider
from vyomel.models.types import ChatMessage, ModelRequest


@pytest.mark.req("FR-707")
def test_vllm_requires_base_url() -> None:
    with pytest.raises(ConfigError):
        VllmProvider(base_url="")


@pytest.mark.req("FR-707")
def test_router_builds_vllm_from_settings() -> None:
    settings = Settings(
        env="test", planner_backend="vllm", vllm_base_url="http://127.0.0.1:8000/v1"
    )
    provider = build_provider(settings, "vllm")
    assert provider.info.name == "vllm"
    assert provider.info.is_remote is True


@pytest.mark.asyncio
@pytest.mark.req("FR-707")
async def test_vllm_against_fixture_server() -> None:
    from evals.suites.serving.fixture_server import FixtureServer

    async with FixtureServer(mode="vllm", max_num_seqs=4) as server:
        provider = VllmProvider(base_url=server.base_url, model="fixture")
        response = await provider.complete(
            ModelRequest(
                purpose="chat",
                messages=(ChatMessage(role="user", content="ping"),),
                max_tokens=16,
            )
        )
        assert response.provider == "vllm"
        assert response.completion_tokens >= 1


@pytest.mark.req("FR-707")
def test_offline_blocks_vllm() -> None:
    from vyomel.core.errors import PrivacyRoutingViolation

    settings = Settings(
        env="dev",
        offline=True,
        planner_backend="vllm",
        vllm_base_url="http://127.0.0.1:8000/v1",
    )
    with pytest.raises(PrivacyRoutingViolation):
        get_planner_provider(settings)
