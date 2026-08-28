"""Injectable clock.

Every timestamp in Astra is timezone-aware UTC. Time is injected rather than
read from ``datetime.now()`` directly so that lease expiry, approval TTLs, and
retry backoff can be tested deterministically instead of with sleeps.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    """Test clock. Advances only when told to."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        self._now += timedelta(seconds=seconds)
        return self._now


_default: Clock = SystemClock()


def utcnow() -> datetime:
    return _default.now()
