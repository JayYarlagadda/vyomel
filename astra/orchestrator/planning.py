"""Task creation with optional NL planning."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from astra.core.config import Settings
from astra.core.plan_spec import HandwrittenPlan
from astra.core.types import Capability, TaskOrigin, Trust
from astra.orchestrator.plans import PlanService
from astra.orchestrator.tasks import TaskBounds, TaskService
from astra.orchestrator.tools import ToolCatalog
from astra.planner.decompose import decompose
from astra.security.audit import AuditEvent, AuditTrail
from astra.store.models import Task
from astra.tools.registry import ToolRegistry


async def create_task(
    session: AsyncSession,
    settings: Settings,
    registry: ToolRegistry,
    *,
    instruction: str,
    origin: TaskOrigin,
    capability_ceiling: Capability,
    context_hints: dict[str, Any],
    bounds: TaskBounds,
    plan_override: HandwrittenPlan | None = None,
    dry_run: bool = False,
) -> Task:
    service = TaskService(session, settings)
    task = await service.create(
        instruction=instruction,
        origin=origin,
        capability_ceiling=capability_ceiling,
        context_hints=context_hints,
        bounds=bounds,
    )
    if plan_override is not None:
        return await PlanService(session, settings, registry).install(
            task, plan_override, activate=not dry_run, trust=Trust.USER
        )

    catalog = ToolCatalog(registry).list()
    result = await decompose(
        instruction,
        catalog=catalog,
        capability_ceiling=capability_ceiling,
        settings=settings,
        registry=registry,
    )
    task.normalized_intent = result.normalized_intent
    await session.flush()
    await AuditTrail().append(
        session,
        actor="planner:decompose",
        event_type=AuditEvent.PLAN_REQUESTED,
        task_id=task.id,
        payload={
            "model": result.model,
            "provider": result.provider,
            "prompt_hash": result.prompt_hash,
            "prompt_version": result.prompt_version,
            "normalized_intent": result.normalized_intent,
        },
    )
    trust = Trust.TOOL_UNTRUSTED if result.provider != "mock-planner" else Trust.USER
    return await PlanService(session, settings, registry).install(
        task,
        result.plan,
        activate=not dry_run,
        trust=trust,
    )
