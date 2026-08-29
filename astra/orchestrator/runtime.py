"""Process-level wiring for the API and CLI.

Those layers do not import ``astra.runtime`` or ``astra.tools`` directly;
they come here. Keeps the layering table honest while still letting
``astra serve`` host the scheduler and ``astra worker`` host a worker.
"""

from __future__ import annotations

import asyncio
import os

from redis.asyncio import Redis

from astra.core.clock import Clock, SystemClock
from astra.core.config import Settings
from astra.core.ids import new_id
from astra.core.logging import configure_logging, get_logger
from astra.runtime.gate import PolicyGate
from astra.runtime.queue import ActionQueue
from astra.runtime.scheduler import Scheduler
from astra.runtime.worker import Worker
from astra.store.db import dispose_engine, init_engine
from astra.tools.registry import ToolRegistry, default_registry

log = get_logger(__name__)

_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = default_registry()
    return _registry


def reset_registry(registry: ToolRegistry | None = None) -> None:
    """Test hook: inject a registry (e.g. with a fake tool) or clear the cache."""
    global _registry
    _registry = registry


def make_queue(settings: Settings, redis: Redis | None = None) -> tuple[Redis, ActionQueue]:
    client = redis or Redis.from_url(settings.redis_url, decode_responses=True)
    return client, ActionQueue(client)


def make_scheduler(
    settings: Settings,
    queue: ActionQueue,
    clock: Clock | None = None,
    *,
    gate: PolicyGate | None = None,
) -> Scheduler:
    return Scheduler(settings, queue, get_registry(), clock=clock or SystemClock(), gate=gate)


def make_worker(
    settings: Settings,
    queue: ActionQueue,
    *,
    worker_id: str | None = None,
    clock: Clock | None = None,
) -> Worker:
    return Worker(
        settings,
        queue,
        get_registry(),
        worker_id=worker_id or f"worker-{os.getpid()}-{new_id()[:6]}",
        clock=clock or SystemClock(),
    )


async def run_worker(settings: Settings) -> None:
    configure_logging(settings)
    settings.ensure_directories()
    init_engine(settings)
    redis, queue = make_queue(settings)
    worker = make_worker(settings, queue)
    try:
        await worker.run_forever()
    finally:
        await redis.aclose()
        await dispose_engine()


class SchedulerHandle:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._redis: Redis | None = None
        self._scheduler: Scheduler | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._redis, queue = make_queue(self._settings)
        self._scheduler = make_scheduler(self._settings, queue)
        self._task = asyncio.create_task(self._scheduler.run_forever(), name="astra-scheduler")
        log.info("astra.scheduler.started")

    async def stop(self) -> None:
        if self._scheduler is not None:
            self._scheduler.stop()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("astra.scheduler.stop_failed")
        if self._redis is not None:
            await self._redis.aclose()
        log.info("astra.scheduler.stopped")
