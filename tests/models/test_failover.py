"""Circuit breaker and failover (FR-705)."""

from __future__ import annotations

import pytest

from vyomel.core.errors import VyomelError
from vyomel.models.circuit import CircuitBreaker, FailoverProvider
from vyomel.models.providers.mock import MockPlannerProvider
from vyomel.models.types import ChatMessage, ModelRequest, ModelResponse, ProviderInfo


class _Flaky:
    def __init__(self, *, fail_times: int) -> None:
        self._remaining = fail_times
        self._info = ProviderInfo(
            name="flaky",
            is_remote=True,
            supports_structured_output=True,
            max_context=8_000,
        )
        self.calls = 0

    @property
    def info(self) -> ProviderInfo:
        return self._info

    async def complete(self, req: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise RuntimeError("upstream 503")
        return ModelResponse(
            content="{}",
            model="flaky",
            provider="flaky",
            prompt_tokens=1,
            completion_tokens=1,
            latency_ms=1.0,
            parsed={},
        )


@pytest.mark.req("FR-705")
def test_breaker_opens_after_consecutive_failures() -> None:
    breaker = CircuitBreaker(name="x", failure_threshold=3, window_size=10)
    assert breaker.allow()
    for _ in range(3):
        breaker.record_failure()
    assert breaker.state.value == 1  # OPEN
    assert breaker.allow() is False


@pytest.mark.asyncio
@pytest.mark.req("FR-705")
async def test_failover_skips_failing_provider() -> None:
    flaky = _Flaky(fail_times=1)
    mock = MockPlannerProvider()
    provider = FailoverProvider([flaky, mock])  # type: ignore[list-item]
    response = await provider.complete(
        ModelRequest(
            purpose="planner.decompose",
            messages=(ChatMessage(role="user", content="User instruction:\nlist D:/tmp"),),
        )
    )
    assert response.provider == "mock-planner"
    assert flaky.calls == 1


@pytest.mark.asyncio
@pytest.mark.req("FR-705")
async def test_failover_raises_when_all_fail() -> None:
    flaky = _Flaky(fail_times=5)
    provider = FailoverProvider([flaky])  # type: ignore[list-item]
    with pytest.raises(VyomelError):
        await provider.complete(
            ModelRequest(
                purpose="chat",
                messages=(ChatMessage(role="user", content="hi"),),
            )
        )
