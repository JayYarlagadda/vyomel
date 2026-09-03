"""Model provider selection (FR-701, FR-702)."""

from __future__ import annotations

from astra.core.config import Settings
from astra.core.errors import ConfigError
from astra.models.providers.mock import MockPlannerProvider
from astra.models.providers.protocol import ModelProvider


def get_planner_provider(settings: Settings) -> ModelProvider:
    backend = settings.planner_backend
    if backend == "auto":
        backend = "mock"
    if backend == "mock":
        return MockPlannerProvider()
    raise ConfigError(
        f"planner backend {backend!r} is not configured; use ASTRA_PLANNER_BACKEND=mock"
    )
