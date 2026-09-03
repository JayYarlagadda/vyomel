"""Long-run durability harness (docs/11-EVALUATION.md §8)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from sqlalchemy import select

from astra.core.config import Settings
from astra.core.types import ActionStatus
from astra.orchestrator.plans import PlanService
from astra.orchestrator.runtime import get_registry, make_scheduler, make_worker
from astra.orchestrator.tasks import TaskService
from astra.planner.longrun import build_research_plan
from astra.runtime.chaos import ResearchAudit, abandon_running_action, audit_research_task
from astra.runtime.queue import ActionQueue
from astra.runtime.reaper import Reaper
from astra.runtime.scheduler import Scheduler
from astra.runtime.worker import Worker
from astra.store.db import session_scope
from astra.store.models import Action, Task


@dataclass(frozen=True, slots=True)
class HarnessResult:
    audit: ResearchAudit
    simulated_kills: int
    elapsed_s: float


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    items: int
    duration_s: float
    kill_interval_s: float | None
    max_rounds: int = 50_000


class LongrunHarness:
    def __init__(
        self,
        settings: Settings,
        queue: ActionQueue,
        *,
        scheduler: Scheduler | None = None,
        worker: Worker | None = None,
    ) -> None:
        self._settings = settings
        self._queue = queue
        self._scheduler = scheduler or make_scheduler(settings, queue)
        self._worker = worker or make_worker(settings, queue, worker_id="longrun-worker")

    async def install(self, *, items: int) -> Task:
        async with session_scope() as session:
            task = await TaskService(session, self._settings).create(
                instruction=f"Research {items} mock pages under chaos."
            )
            return await PlanService(session, self._settings, get_registry()).install(
                task, build_research_plan(items=items)
            )

    async def run(
        self,
        task_id: str,
        *,
        expected_fetches: int,
        config: HarnessConfig,
    ) -> HarnessResult:
        await self._scheduler.recover()
        start = time.monotonic()
        last_kill = start
        kills = 0
        for _round in range(config.max_rounds):
            elapsed = time.monotonic() - start
            if elapsed >= config.duration_s:
                break
            published = await self._scheduler.tick()
            if (
                config.kill_interval_s is not None
                and time.monotonic() - last_kill >= config.kill_interval_s
                and await abandon_running_action(task_id) is not None
            ):
                kills += 1
                last_kill = time.monotonic()
                await Reaper().reap()
            worked = await self._worker.run_once(block_ms=50)
            task = await self._load_task(task_id)
            if task.status.is_terminal:
                break
            if published == 0 and not worked:
                await asyncio.sleep(0.05)
        audit = await audit_research_task(task_id, expected_fetches=expected_fetches)
        return HarnessResult(
            audit=audit,
            simulated_kills=kills,
            elapsed_s=time.monotonic() - start,
        )

    async def flush_redis_stream(self) -> None:
        await self._queue.reset_stream()

    async def recover(self) -> None:
        await self._scheduler.recover()

    @staticmethod
    async def _load_task(task_id: str) -> Task:
        async with session_scope() as session:
            task = await session.get(Task, task_id)
            if task is None:
                raise ValueError(f"unknown task {task_id}")
            return task


async def count_in_flight(task_id: str) -> int:
    async with session_scope() as session:
        return len(
            list(
                (
                    await session.execute(
                        select(Action).where(
                            Action.task_id == task_id,
                            Action.status.in_(
                                (ActionStatus.DISPATCHED, ActionStatus.RUNNING, ActionStatus.READY)
                            ),
                        )
                    )
                ).scalars()
            )
        )


MODES = {
    "fast": HarnessConfig(items=10, duration_s=120.0, kill_interval_s=2.0),
    "standard": HarnessConfig(items=100, duration_s=600.0, kill_interval_s=10.0),
    "chaos": HarnessConfig(items=100, duration_s=1_200.0, kill_interval_s=60.0),
}
