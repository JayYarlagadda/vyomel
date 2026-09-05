"""Model router (FR-702)."""

from __future__ import annotations

import pytest

from vyomel.core.config import Settings
from vyomel.models.router import get_planner_provider


@pytest.mark.req("FR-702")
def test_router_selects_mock_by_default(settings: Settings) -> None:
    provider = get_planner_provider(settings)
    assert provider.info.name == "mock-planner"


@pytest.mark.req("FR-702")
def test_router_selects_mock_alt_variant() -> None:
    settings = Settings(env="test", planner_backend="mock-alt")
    provider = get_planner_provider(settings)
    assert provider.info.name == "mock-planner"
