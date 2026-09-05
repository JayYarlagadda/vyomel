"""Handwritten plans (FR-107).

M1 has no LLM. A plan is a validated DAG of tool invocations the user (or a
test) supplies. The runtime executes it; this module only persists it.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from vyomel.core.clock import Clock, SystemClock
from vyomel.core.config import Settings
from vyomel.core.errors import VyomelError, ErrorCode
from vyomel.core.ids import content_hash, idempotency_key, new_id
from vyomel.core.plan_spec import ActionSpec, HandwrittenPlan, StepSpec
from vyomel.core.types import ActionStatus, StepStatus, TaskStatus, Trust
from vyomel.runtime.dag import ActionNode, CyclicPlanError, validate_acyclic
from vyomel.runtime.state import TaskTrigger, apply_task
from vyomel.security.audit import AuditEvent, AuditTrail
from vyomel.security.capability import EscalationRules, Invocation, classify
from vyomel.security.policy import store_for
from vyomel.store.models import Action, Step, StepEdge, Task
from vyomel.store.repos import TaskRepo
from vyomel.tools.registry import RegistryError, ToolRegistry


class PlanError(VyomelError):
    code = ErrorCode.INVALID_PARAMETERS


# Re-exported for callers that imported plan types from here before M5.
__all__ = [
    "ActionSpec",
    "HandwrittenPlan",
    "PlanError",
    "PlanService",
    "StepSpec",
]


class PlanService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        registry: ToolRegistry,
        escalation: EscalationRules | None = None,
        *,
        clock: Clock | None = None,
        audit: AuditTrail | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._registry = registry
        self._audit = audit or AuditTrail(clock or SystemClock())
        # Classification happens here, at plan install, because the capability
        # level is a property of the tool *and its resolved parameters* — and
        # this is the first moment both exist. The gate re-reads the stored
        # level at dispatch; it does not re-derive it.
        self._escalation = escalation or store_for(settings).get().escalation

    async def install(
        self,
        task: Task,
        plan: HandwrittenPlan,
        *,
        activate: bool = True,
        trust: Trust = Trust.USER,
    ) -> Task:
        """Persist a classified DAG. ``activate=False`` is dry-run: the rows exist
        so ``GET /plan`` is truthful, but the task stays ``PLANNING`` and the
        scheduler will not dispatch.
        """
        if len(plan.steps) > self._settings.max_steps:
            raise PlanError(f"plan has {len(plan.steps)} steps; max is {self._settings.max_steps}")

        repo = TaskRepo(self._session)
        planning = apply_task(task.status, TaskTrigger.PLAN_REQUESTED)
        moved = await repo.cas_status(task.id, expected=task.status, new=planning)
        if moved is None:
            raise PlanError(f"task {task.id} could not enter PLANNING from {task.status}")

        try:
            version = (task.plan_version or 0) + 1
            steps, actions, edges = self._materialize(
                task, plan, trust=trust, plan_version=version, start_ordinal=0
            )
        except (PlanError, CyclicPlanError) as exc:
            dest = apply_task(TaskStatus.PLANNING, TaskTrigger.PLAN_INVALID)
            await repo.cas_status(
                task.id,
                expected=TaskStatus.PLANNING,
                new=dest,
                error={"code": "INVALID_PLAN", "message": str(exc)},
            )
            raise PlanError(str(exc)) from exc

        self._session.add_all(steps)
        self._session.add_all(edges)
        self._session.add_all(actions)
        await self._session.flush()

        if activate:
            dest = apply_task(TaskStatus.PLANNING, TaskTrigger.PLAN_VALIDATED)
            version = (task.plan_version or 0) + 1
            updated = await repo.cas_status(
                task.id, expected=TaskStatus.PLANNING, new=dest, plan_version=version
            )
            assert updated is not None
        else:
            # dry_run: persist the classified DAG but do not enter READY, so the
            # scheduler cannot dispatch. PLANNING is not in runnable().
            task.plan_version = (task.plan_version or 0) + 1
            await self._session.flush()
            updated = task
        version = updated.plan_version
        await self._audit.append(
            self._session,
            actor="orchestrator:handwritten" if trust is Trust.USER else "orchestrator:planner",
            event_type=AuditEvent.PLAN_INSTALLED,
            task_id=task.id,
            capability_level=max(a.capability_level for a in actions),
            payload={
                "plan_hash": content_hash(plan.model_dump(mode="json")),
                "plan_version": version,
                "steps": len(steps),
                "actions": [{"tool": a.tool, "level": a.capability_level.value} for a in actions],
            },
        )
        return updated

    async def append_replan(
        self,
        task: Task,
        plan: HandwrittenPlan,
        *,
        trust: Trust = Trust.TOOL_UNTRUSTED,
    ) -> Task:
        existing_steps, _ = await self.load(task.id)
        version = task.plan_version + 1
        steps, actions, edges = self._materialize(
            task,
            plan,
            trust=trust,
            plan_version=version,
            start_ordinal=len(existing_steps),
        )
        self._session.add_all(steps)
        self._session.add_all(edges)
        self._session.add_all(actions)
        task.plan_version = version
        await self._session.flush()
        await self._audit.append(
            self._session,
            actor="orchestrator:replan",
            event_type=AuditEvent.PLAN_INSTALLED,
            task_id=task.id,
            capability_level=max(a.capability_level for a in actions),
            payload={
                "plan_hash": content_hash(plan.model_dump(mode="json")),
                "plan_version": version,
                "replan": True,
                "steps": len(steps),
            },
        )
        return task

    async def load(self, task_id: str) -> tuple[list[Step], list[Action]]:
        from vyomel.store.repos import ActionRepo, StepRepo

        steps = await StepRepo(self._session).list_by_task(task_id)
        actions = await ActionRepo(self._session).list_by_task(task_id)
        return steps, actions

    def _materialize(
        self,
        task: Task,
        plan: HandwrittenPlan,
        *,
        trust: Trust,
        plan_version: int,
        start_ordinal: int,
    ) -> tuple[list[Step], list[Action], list[StepEdge]]:
        step_ids = {spec.alias: new_id() for spec in plan.steps}
        action_ids = {a.alias: new_id() for s in plan.steps for a in s.actions}
        action_step = {a.alias: s.alias for s in plan.steps for a in s.actions}

        known_steps = set(step_ids)
        known_actions = set(action_ids)
        for spec in plan.steps:
            for dep in spec.depends_on:
                if dep not in known_steps:
                    raise PlanError(f"step {spec.alias} depends on unknown step {dep}")
            for action in spec.actions:
                for dep in action.depends_on:
                    if dep not in known_actions:
                        raise PlanError(f"action {action.alias} depends on unknown action {dep}")

        # Step-level deps become action-level deps from every action in the
        # upstream step to every action in this step, unless the action named
        # its own deps. Explicit action deps win.
        implicit: dict[str, list[str]] = {alias: [] for alias in action_ids}
        for spec in plan.steps:
            if not spec.depends_on:
                continue
            upstream_actions = [
                a.alias for s in plan.steps if s.alias in spec.depends_on for a in s.actions
            ]
            for action in spec.actions:
                if not action.depends_on:
                    implicit[action.alias].extend(upstream_actions)

        nodes: list[ActionNode] = []
        for spec in plan.steps:
            for action in spec.actions:
                deps = tuple(action.depends_on or implicit[action.alias])
                nodes.append(
                    ActionNode(
                        id=action.alias,
                        status=ActionStatus.PLANNED,
                        depends_on=deps,
                        step_id=spec.alias,
                        tolerates_unverified=spec.tolerates_unverified,
                    )
                )
        validate_acyclic(nodes)

        steps: list[Step] = []
        edges: list[StepEdge] = []
        actions: list[Action] = []
        for ordinal, spec in enumerate(plan.steps):
            step_id = step_ids[spec.alias]
            steps.append(
                Step(
                    id=step_id,
                    task_id=task.id,
                    ordinal=start_ordinal + ordinal,
                    title=spec.title,
                    intent=spec.intent,
                    status=StepStatus.PLANNED,
                    plan_version=plan_version,
                    depends_on=[step_ids[d] for d in spec.depends_on],
                    tolerates_unverified=spec.tolerates_unverified,
                )
            )
            for dep in spec.depends_on:
                edges.append(
                    StepEdge(
                        task_id=task.id,
                        from_step_id=step_ids[dep],
                        to_step_id=step_id,
                        plan_version=plan_version,
                    )
                )
            if spec.required_capability and spec.required_capability > task.capability_ceiling:
                raise PlanError(
                    f"step {spec.alias} requires {spec.required_capability}, "
                    f"task ceiling is {task.capability_ceiling}"
                )
            for action in spec.actions:
                try:
                    tool = self._registry.get(action.tool)
                except RegistryError as exc:
                    raise PlanError(str(exc)) from exc
                try:
                    parsed = tool.Input.model_validate(action.parameters)
                except Exception as exc:
                    raise PlanError(
                        f"action {action.alias}: invalid parameters for {action.tool}: {exc}"
                    ) from exc
                parameters = parsed.model_dump(mode="json")
                classification = classify(
                    Invocation(
                        tool=tool.name,
                        parameters=parameters,
                        base=tool.classify(parsed),
                        actuation_tier=tool.actuation_tier,
                        # Handwritten plans are USER trust; planner output is
                        # TOOL_UNTRUSTED until boundary markers exist (M5).
                        trust=trust,
                    ),
                    self._escalation,
                )
                capability = classification.level
                if capability > task.capability_ceiling:
                    why = classification.reasons
                    reasons = f" ({', '.join(why)})" if why else ""
                    raise PlanError(
                        f"action {action.alias} requires {capability}{reasons}, task ceiling is "
                        f"{task.capability_ceiling}"
                    )
                dep_aliases = action.depends_on or implicit[action.alias]
                max_retries = (
                    action.max_retries
                    if action.max_retries is not None
                    else self._settings.max_retries
                )
                timeout_s = action.timeout_s or min(
                    tool.default_timeout_s, self._settings.action_timeout_s
                )
                actions.append(
                    Action(
                        id=action_ids[action.alias],
                        task_id=task.id,
                        step_id=step_ids[action_step[action.alias]],
                        tool=tool.name,
                        tool_version=tool.version,
                        parameters=parameters,
                        preconditions=action.preconditions,
                        postconditions=action.postconditions,
                        capability_level=capability,
                        reversible=tool.reversible,
                        idempotency_key=idempotency_key(
                            tool=tool.name,
                            parameters=parameters,
                            task_id=task.id,
                            step_id=step_id,
                            plan_version=plan_version,
                        ),
                        depends_on=[action_ids[d] for d in dep_aliases],
                        status=ActionStatus.PLANNED,
                        max_retries=max_retries,
                        timeout_s=timeout_s,
                    )
                )
        return steps, actions, edges
