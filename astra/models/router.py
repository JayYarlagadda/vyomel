"""Model provider selection (FR-701, FR-702, FR-703)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from astra.core.config import Settings
from astra.core.errors import ConfigError, PrivacyRoutingViolation
from astra.core.types import Sensitivity
from astra.models.accounting import AccountingProvider
from astra.models.cache import CachedProvider
from astra.models.providers.mock import MockPlannerProvider
from astra.models.providers.openai_compat import OpenAICompatibleProvider
from astra.models.providers.protocol import ModelProvider
from astra.models.types import ModelRequest


def get_planner_provider(
    settings: Settings,
    *,
    session: AsyncSession | None = None,
    task_id: str | None = None,
    sensitivity: Sensitivity = Sensitivity.PUBLIC,
) -> ModelProvider:
    backend = settings.planner_backend
    if backend == "auto":
        backend = "mock"
    inner: ModelProvider
    if backend == "mock":
        inner = MockPlannerProvider(variant="v1")
    elif backend == "mock-alt":
        inner = MockPlannerProvider(variant="v2")
    elif backend in {"openai", "local"}:
        if sensitivity is Sensitivity.SENSITIVE and settings.env != "test":
            raise PrivacyRoutingViolation(
                "sensitive planner input cannot be sent to a remote provider"
            )
        if backend == "openai":
            inner = OpenAICompatibleProvider(
                name="openai",
                base_url="https://api.openai.com/v1",
                api_key=settings.openai_api_key.get_secret_value(),
                model="gpt-4o-mini",
                is_remote=True,
            )
        else:
            inner = OpenAICompatibleProvider(
                name="local",
                base_url=settings.local_model_base_url,
                api_key="",
                model="local",
                is_remote=False,
            )
    else:
        raise ConfigError(
            f"planner backend {backend!r} is not configured; use ASTRA_PLANNER_BACKEND=mock"
        )

    if settings.offline and inner.info.is_remote:
        raise PrivacyRoutingViolation("offline mode permits local providers only")

    if settings.env == "test":
        inner = CachedProvider(inner)
    if session is not None:
        inner = AccountingProvider(inner, session, task_id=task_id)
    return inner


def route_for_request(settings: Settings, req: ModelRequest) -> ModelProvider:
    return get_planner_provider(settings, sensitivity=req.sensitivity)
