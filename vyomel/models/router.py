"""Model provider selection (FR-701, FR-702, FR-703, FR-705)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from vyomel.core.config import Settings
from vyomel.core.errors import ConfigError, PrivacyRoutingViolation
from vyomel.core.types import Sensitivity
from vyomel.models.accounting import AccountingProvider
from vyomel.models.cache import CachedProvider
from vyomel.models.catalog import load_model_config, preferred_backends
from vyomel.models.circuit import CircuitBreaker, FailoverProvider
from vyomel.models.providers.mock import MockPlannerProvider
from vyomel.models.providers.openai_compat import OpenAICompatibleProvider
from vyomel.models.providers.protocol import ModelProvider
from vyomel.models.providers.vllm import VllmProvider
from vyomel.models.types import ModelPurpose, ModelRequest

_BREAKERS: dict[str, CircuitBreaker] = {}


def _breaker(name: str) -> CircuitBreaker:
    if name not in _BREAKERS:
        _BREAKERS[name] = CircuitBreaker(name=name)
    return _BREAKERS[name]


def reset_breakers() -> None:
    _BREAKERS.clear()


def build_provider(settings: Settings, backend: str) -> ModelProvider:
    if backend in {"mock", "mock-planner"}:
        return MockPlannerProvider(variant="v1")
    if backend == "mock-alt":
        return MockPlannerProvider(variant="v2")
    if backend in {"openai", "cloud"}:
        return OpenAICompatibleProvider(
            name="openai",
            base_url="https://api.openai.com/v1",
            api_key=settings.openai_api_key.get_secret_value(),
            model="gpt-4o-mini",
            is_remote=True,
        )
    if backend == "local":
        return OpenAICompatibleProvider(
            name="local",
            base_url=settings.local_model_base_url,
            api_key="",
            model="local",
            is_remote=False,
        )
    if backend == "vllm":
        return VllmProvider.from_settings(settings)
    raise ConfigError(
        f"planner backend {backend!r} is not configured; "
        "use VYOMEL_PLANNER_BACKEND=mock|local|openai|vllm"
    )


def _eligible(
    provider: ModelProvider,
    *,
    settings: Settings,
    sensitivity: Sensitivity,
) -> bool:
    if settings.offline and provider.info.is_remote:
        return False
    return not (sensitivity is Sensitivity.SENSITIVE and provider.info.is_remote)


def get_planner_provider(
    settings: Settings,
    *,
    session: AsyncSession | None = None,
    task_id: str | None = None,
    sensitivity: Sensitivity = Sensitivity.PUBLIC,
    purpose: ModelPurpose = "planner.decompose",
) -> ModelProvider:
    backend = settings.planner_backend
    if backend == "auto":
        cfg = load_model_config("config/models.yaml")
        order = preferred_backends(purpose, config=cfg)
        if settings.env == "test" or not settings.openai_api_key.get_secret_value():
            order = ["mock", *[b for b in order if b != "mock"]]
        providers: list[ModelProvider] = []
        for name in order:
            try:
                candidate = build_provider(settings, name)
            except ConfigError:
                continue
            if _eligible(candidate, settings=settings, sensitivity=sensitivity):
                providers.append(candidate)
        if not providers:
            if sensitivity is Sensitivity.SENSITIVE or settings.offline:
                raise PrivacyRoutingViolation(
                    "no eligible local provider for sensitive/offline request"
                )
            raise ConfigError("no eligible model provider for auto routing")
        inner: ModelProvider
        if len(providers) == 1:
            inner = providers[0]
        else:
            inner = FailoverProvider(
                providers,
                breakers={p.info.name: _breaker(p.info.name) for p in providers},
            )
    else:
        if (
            sensitivity is Sensitivity.SENSITIVE
            and backend in {"openai", "cloud", "vllm"}
            and settings.env != "test"
        ):
            raise PrivacyRoutingViolation(
                "sensitive planner input cannot be sent to a remote provider"
            )
        if settings.offline and backend in {"openai", "cloud", "vllm"}:
            raise PrivacyRoutingViolation("offline mode permits local providers only")
        inner = build_provider(settings, backend)
        if settings.offline and inner.info.is_remote:
            raise PrivacyRoutingViolation("offline mode permits local providers only")

    if settings.env == "test":
        inner = CachedProvider(inner)
    if session is not None:
        inner = AccountingProvider(inner, session, task_id=task_id)
    return inner


def route_for_request(settings: Settings, req: ModelRequest) -> ModelProvider:
    return get_planner_provider(
        settings,
        sensitivity=req.sensitivity,
        purpose=req.purpose,
    )
