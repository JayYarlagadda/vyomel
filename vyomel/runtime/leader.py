"""Redis leader election for the scheduler process (M13).

Multiple scheduler replicas may run; only the lock holder ticks. Loss of the
lock (TTL expiry after a crash) lets another replica recover orphans and continue.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from redis.asyncio import Redis

from vyomel.core.logging import get_logger

log = get_logger(__name__)


class LeaderElector:
    """``SET key id NX EX ttl`` + periodic renew while holding the lock."""

    def __init__(
        self,
        redis: Redis,
        *,
        key: str,
        ttl_s: int,
        holder_id: str | None = None,
    ) -> None:
        self._redis = redis
        self._key = key
        self._ttl_s = ttl_s
        self._holder_id = holder_id or f"scheduler-{uuid.uuid4().hex[:12]}"
        self._held = False

    @property
    def holder_id(self) -> str:
        return self._holder_id

    @property
    def held(self) -> bool:
        return self._held

    async def try_acquire(self) -> bool:
        ok = await self._redis.set(self._key, self._holder_id, nx=True, ex=self._ttl_s)
        self._held = bool(ok)
        if self._held:
            log.info("vyomel.scheduler.leader_acquired", holder=self._holder_id, key=self._key)
        return self._held

    async def renew(self) -> bool:
        """Extend TTL only if we still own the lock."""
        # Lua: renew if value matches holder.
        script = """
        if redis.call('GET', KEYS[1]) == ARGV[1] then
          return redis.call('EXPIRE', KEYS[1], ARGV[2])
        else
          return 0
        end
        """
        result: Any = await self._redis.eval(
            script, 1, self._key, self._holder_id, str(self._ttl_s)
        )
        self._held = bool(result)
        if not self._held:
            log.warning("vyomel.scheduler.leader_lost", holder=self._holder_id)
        return self._held

    async def release(self) -> None:
        script = """
        if redis.call('GET', KEYS[1]) == ARGV[1] then
          return redis.call('DEL', KEYS[1])
        else
          return 0
        end
        """
        await self._redis.eval(script, 1, self._key, self._holder_id)
        self._held = False

    async def run_as_leader(self, work: Any, *, renew_every_s: float | None = None) -> None:
        """Acquire (or wait), then call ``work()`` while renewing the lock.

        ``work`` is an async callable that runs one unit of work (e.g. scheduler.tick).
        """
        interval = renew_every_s if renew_every_s is not None else max(1.0, self._ttl_s / 3)
        while True:
            if not self._held and not await self.try_acquire():
                await asyncio.sleep(interval)
                continue
            if not await self.renew():
                await asyncio.sleep(interval)
                continue
            await work()
            await asyncio.sleep(0.5)
