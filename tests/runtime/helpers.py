"""Helpers for runtime integration tests. Not fixtures — those stay in conftest."""

from __future__ import annotations

from typing import Any

from astra.core.config import Settings
from astra.core.types import Capability
from astra.orchestrator.plans import HandwrittenPlan, PlanService
from astra.orchestrator.runtime import get_registry
from astra.orchestrator.tasks import TaskService
from astra.runtime.scheduler import Scheduler
from astra.runtime.worker import Worker
from astra.store.db import session_scope
from astra.store.models import Task


async def drain(scheduler: Scheduler, worker: Worker, *, rounds: int = 40) -> None:
    for _ in range(rounds):
        published = await scheduler.tick()
        worked = await worker.run_once(block_ms=50)
        if published == 0 and not worked:
            await scheduler.tick()
            return
    raise AssertionError("runtime did not drain within the round budget")


async def install_plan(
    settings: Settings,
    plan: HandwrittenPlan,
    *,
    instruction: str = "test",
    ceiling: Capability = Capability.L2,
    context_hints: dict[str, Any] | None = None,
) -> Task:
    async with session_scope() as session:
        task = await TaskService(session, settings).create(
            instruction=instruction,
            capability_ceiling=ceiling,
            context_hints=context_hints or {},
        )
        return await PlanService(session, settings, get_registry()).install(task, plan)
