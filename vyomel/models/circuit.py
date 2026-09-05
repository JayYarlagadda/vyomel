"""Provider circuit breaker and failover (FR-705, docs/09 §3.3)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import IntEnum
from threading import Lock

from vyomel.core.errors import ErrorCode, VyomelError
from vyomel.models.providers.protocol import ModelProvider
from vyomel.models.types import ModelRequest, ModelResponse, ProviderInfo
from vyomel.obs.metrics import CIRCUIT_BREAKER, MODEL_FAILOVERS


class BreakerState(IntEnum):
    CLOSED = 0
    OPEN = 1
    HALF_OPEN = 2


@dataclass
class CircuitBreaker:
    """Per-provider breaker: 5 consecutive failures or ≥50 % errors over 20 calls."""

    name: str
    failure_threshold: int = 5
    window_size: int = 20
    open_for: timedelta = field(default_factory=lambda: timedelta(seconds=60))
    _failures: int = 0
    _outcomes: list[bool] = field(default_factory=list)
    _opened_at: datetime | None = None
    _state: BreakerState = BreakerState.CLOSED
    _lock: Lock = field(default_factory=Lock)

    @property
    def state(self) -> BreakerState:
        with self._lock:
            self._maybe_half_open()
            return self._state

    def allow(self) -> bool:
        with self._lock:
            self._maybe_half_open()
            return self._state is not BreakerState.OPEN

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._outcomes.append(True)
            self._trim()
            self._state = BreakerState.CLOSED
            self._opened_at = None
            CIRCUIT_BREAKER.labels(provider=self.name).set(float(BreakerState.CLOSED))

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._outcomes.append(False)
            self._trim()
            rate = self._error_rate()
            if self._failures >= self.failure_threshold or (
                len(self._outcomes) >= self.window_size and rate >= 0.5
            ):
                self._state = BreakerState.OPEN
                self._opened_at = datetime.now(UTC)
                CIRCUIT_BREAKER.labels(provider=self.name).set(float(BreakerState.OPEN))

    def _maybe_half_open(self) -> None:
        if (
            self._state is BreakerState.OPEN
            and self._opened_at is not None
            and datetime.now(UTC) - self._opened_at >= self.open_for
        ):
            self._state = BreakerState.HALF_OPEN
            CIRCUIT_BREAKER.labels(provider=self.name).set(float(BreakerState.HALF_OPEN))

    def _trim(self) -> None:
        if len(self._outcomes) > self.window_size:
            self._outcomes = self._outcomes[-self.window_size :]

    def _error_rate(self) -> float:
        if not self._outcomes:
            return 0.0
        return sum(1 for ok in self._outcomes if not ok) / len(self._outcomes)


class FailoverProvider:
    """Try providers in order; skip open breakers; never relax privacy constraints."""

    def __init__(
        self,
        providers: Sequence[ModelProvider],
        *,
        breakers: dict[str, CircuitBreaker] | None = None,
    ) -> None:
        if not providers:
            raise ValueError("FailoverProvider requires at least one provider")
        self._providers = list(providers)
        self._breakers = breakers or {
            p.info.name: CircuitBreaker(name=p.info.name) for p in self._providers
        }

    @property
    def info(self) -> ProviderInfo:
        return self._providers[0].info

    async def complete(self, req: ModelRequest) -> ModelResponse:
        errors: list[str] = []
        for index, provider in enumerate(self._providers):
            breaker = self._breakers[provider.info.name]
            if not breaker.allow():
                errors.append(f"{provider.info.name}:circuit_open")
                continue
            try:
                response = await provider.complete(req)
            except Exception as exc:
                breaker.record_failure()
                errors.append(f"{provider.info.name}:{type(exc).__name__}")
                if index + 1 < len(self._providers):
                    nxt = self._providers[index + 1]
                    MODEL_FAILOVERS.labels(
                        from_provider=provider.info.name,
                        to=nxt.info.name,
                        reason=type(exc).__name__,
                    ).inc()
                continue
            breaker.record_success()
            return response
        raise VyomelError(
            "all model providers failed or are circuit-open",
            code=ErrorCode.TRANSIENT_IO,
            detail={"errors": errors},
            retryable=True,
        )
