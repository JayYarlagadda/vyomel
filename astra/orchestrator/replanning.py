"""Runtime replanning driver (FR-106)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from astra.core.config import Settings
from astra.core.types import ActionStatus, StepStatus, Trust
from astra.orchestrator.plans import PlanService
from astra.orchestrator.tools import ToolCatalog
from astra.planner.replan import replan
from astra.runtime.replan_gate import ReplanGate
from astra.security.audit import AuditEvent, AuditTrail
from astra.store.models import Action, Step, Task
from astra.store.repos import ActionRepo, StepRepo
from astra.tools.registry import ToolRegistry


class OrchestratorReplanGate:
    def __init__(self, settings: Settings, registry: ToolRegistry) -> None:
        self._settings = settings
        self._registry = registry

    async def try_replan(
        self,
        session: AsyncSession,
        task: Task,
        actions: list[Action],
        steps: list[Step],
    ) -> bool:
        if task.replan_count >= self._settings.max_replans:
            return False
        failed = [a for a in actions if a.status is ActionStatus.FAILED]
        if not failed:
            return False

        failed_action = failed[0]
        error_payload = failed_action.error or {}
        code = str(error_payload.get("code", ""))
        if code in {"VERIFICATION_FAILED", "PERMISSION_DENIED"}:
            return False
        step = next(s for s in steps if s.id == failed_action.step_id)
        error = str(error_payload.get("message", error_payload))
        catalog = ToolCatalog(self._registry).list()
        result = await replan(
            instruction=task.instruction,
            failed_step=step.title,
            error=error,
            observation=error,
            catalog=catalog,
            capability_ceiling=task.capability_ceiling,
            settings=self._settings,
            registry=self._registry,
        )
        await ActionRepo(session).cas_status(
            failed_action.id,
            expected=ActionStatus.FAILED,
            new=ActionStatus.CANCELLED,
        )
        await PlanService(session, self._settings, self._registry).append_replan(
            task,
            result.plan,
            trust=Trust.TOOL_UNTRUSTED if result.provider != "mock-planner" else Trust.USER,
        )
        task.replan_count += 1
        await session.flush()
        await AuditTrail().append(
            session,
            actor="planner:replan",
            event_type=AuditEvent.PLAN_REQUESTED,
            task_id=task.id,
            payload={
                "model": result.model,
                "provider": result.provider,
                "prompt_hash": result.prompt_hash,
                "replan_count": task.replan_count,
                "failed_action": failed_action.id,
            },
        )
        # Re-sync step statuses after cancelling the failed action.
        by_step: dict[str, list[Action]] = {}
        refreshed = await ActionRepo(session).list_by_task(task.id)
        for action in refreshed:
            by_step.setdefault(action.step_id, []).append(action)
        step_repo = StepRepo(session)
        for item in steps:
            group = by_step.get(item.id, [])
            if (
                group
                and not any(a.status is ActionStatus.FAILED for a in group)
                and any(not a.status.is_terminal for a in group)
            ):
                await step_repo.set_status(item.id, StepStatus.RUNNING)
        return True


def make_replan_gate(settings: Settings, registry: ToolRegistry) -> ReplanGate:
    return OrchestratorReplanGate(settings, registry)
