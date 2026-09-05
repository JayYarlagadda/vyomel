"""Task creation with optional NL planning."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from vyomel.core.config import Settings
from vyomel.core.plan_spec import HandwrittenPlan
from vyomel.core.types import Capability, TaskOrigin, Trust
from vyomel.orchestrator.plans import PlanService
from vyomel.orchestrator.tasks import TaskBounds, TaskService
from vyomel.orchestrator.tools import ToolCatalog
from vyomel.planner.budget import enforce_token_budget
from vyomel.planner.decompose import decompose
from vyomel.security.audit import AuditEvent, AuditTrail
from vyomel.store.models import Task
from vyomel.tools.registry import ToolRegistry


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
        session=session,
        task_id=task.id,
    )
    enforce_token_budget(
        result.plan,
        token_budget=task.token_budget,
        instruction_chars=len(instruction),
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
