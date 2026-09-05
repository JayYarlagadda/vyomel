"""Process-level wiring for the API and CLI.

Those layers do not import ``vyomel.runtime`` or ``vyomel.tools`` directly;
they come here. Keeps the layering table honest while still letting
``vyomel serve`` host the scheduler and ``vyomel worker`` host a worker.
"""

from __future__ import annotations

import asyncio
import os

from redis.asyncio import Redis

from vyomel.core.clock import Clock, SystemClock
from vyomel.core.config import Settings
from vyomel.core.ids import new_id
from vyomel.core.logging import configure_logging, get_logger
from vyomel.orchestrator.replanning import make_replan_gate
from vyomel.runtime.gate import PolicyGate
from vyomel.runtime.queue import ActionQueue
from vyomel.runtime.scheduler import Scheduler
from vyomel.runtime.worker import Worker
from vyomel.store.db import dispose_engine, init_engine
from vyomel.tools.registry import ToolRegistry, default_registry

log = get_logger(__name__)

_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        from vyomel.core.config import get_settings

        settings = get_settings()
        _registry = default_registry(include_host_tools=not settings.disable_host_tools)
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
    return Scheduler(
        settings,
        queue,
        get_registry(),
        clock=clock or SystemClock(),
        gate=gate,
        replan_gate=make_replan_gate(settings, get_registry()),
    )


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


async def run_scheduler(settings: Settings) -> None:
    """Standalone scheduler with Redis leader election (M13)."""
    from vyomel.runtime.leader import LeaderElector

    configure_logging(settings)
    settings.ensure_directories()
    init_engine(settings)
    redis, queue = make_queue(settings)
    scheduler = make_scheduler(settings, queue)
    if settings.local_agent_routing:
        from vyomel.orchestrator.local_agent import get_local_agent_hub

        scheduler.set_host_bridge(get_local_agent_hub())
    elector = LeaderElector(
        redis,
        key=settings.scheduler_lock_key,
        ttl_s=settings.scheduler_lock_ttl_s,
    )

    async def _tick_if_leader() -> None:
        if (elector.held or await elector.try_acquire()) and await elector.renew():
            await scheduler.tick()

    try:
        # First acquire does recover once.
        while True:
            if await elector.try_acquire():
                break
            await asyncio.sleep(max(1.0, settings.scheduler_lock_ttl_s / 3))
        await scheduler.recover()
        log.info("vyomel.scheduler.process_started", holder=elector.holder_id)
        while True:
            await _tick_if_leader()
            await asyncio.sleep(0.5)
    finally:
        scheduler.stop()
        await elector.release()
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
        if self._settings.local_agent_routing:
            from vyomel.orchestrator.local_agent import get_local_agent_hub

            self._scheduler.set_host_bridge(get_local_agent_hub())
        self._task = asyncio.create_task(self._scheduler.run_forever(), name="vyomel-scheduler")
        log.info("vyomel.scheduler.started")

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
                log.exception("vyomel.scheduler.stop_failed")
        if self._redis is not None:
            await self._redis.aclose()
        log.info("vyomel.scheduler.stopped")
