"""Redis leader election (M13)."""

from __future__ import annotations

from typing import Any

import pytest

from vyomel.runtime.leader import LeaderElector


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        if ex is not None:
            self.ttl[key] = ex
        return True

    async def eval(self, script: str, numkeys: int, *args: Any) -> int:
        key, holder, *rest = args
        if "EXPIRE" in script:
            ttl = int(rest[0])
            if self.store.get(key) == holder:
                self.ttl[key] = ttl
                return 1
            return 0
        if "DEL" in script:
            if self.store.get(key) == holder:
                self.store.pop(key, None)
                return 1
            return 0
        return 0


@pytest.mark.asyncio
async def test_only_one_holder_acquires_lock() -> None:
    redis = _FakeRedis()
    a = LeaderElector(redis, key="lock", ttl_s=10, holder_id="a")  # type: ignore[arg-type]
    b = LeaderElector(redis, key="lock", ttl_s=10, holder_id="b")  # type: ignore[arg-type]
    assert await a.try_acquire() is True
    assert await b.try_acquire() is False
    assert await a.renew() is True
    assert await b.renew() is False
    await a.release()
    assert await b.try_acquire() is True
