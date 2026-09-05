"""Assemble a per-task trace tree from persisted rows (FR-804)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from vyomel.obs.timeline import TraceNode, duration_seconds
from vyomel.store.models import Action, Task
from vyomel.store.repos import ActionRepo, StepRepo, VerificationRepo


async def build_trace(session: AsyncSession, task: Task) -> TraceNode:
    steps = await StepRepo(session).list_by_task(task.id)
    actions = await ActionRepo(session).list_by_task(task.id)
    by_step: dict[str, list[Action]] = {}
    for action in actions:
        by_step.setdefault(action.step_id, []).append(action)
    children: list[TraceNode] = []
    for step in sorted(steps, key=lambda item: item.ordinal):
        action_nodes = [await _action_node(session, action) for action in by_step.get(step.id, [])]
        children.append(
            TraceNode(
                name=f"step {step.ordinal}  {step.title}",
                status=step.status.value,
                children=tuple(action_nodes),
            )
        )
    return TraceNode(
        name=f"{task.id} {task.instruction[:40]!r}",
        status=task.status.value,
        duration_s=duration_seconds(task.started_at, task.finished_at),
        children=tuple(children),
        attributes={"trace_id": task.trace_id, "plan_version": task.plan_version},
    )


async def _action_node(session: AsyncSession, action: Action) -> TraceNode:
    checks = await VerificationRepo(session).list_by_action(action.id)
    verify_kids = tuple(
        TraceNode(
            name=f"verify ({check.verifier})",
            status=check.outcome.value,
            duration_s=(check.latency_ms or 0) / 1000.0,
        )
        for check in checks
    )
    return TraceNode(
        name=f"{action.tool}  {action.capability_level.value}",
        status=action.status.value,
        duration_s=duration_seconds(action.started_at, action.finished_at),
        children=verify_kids,
        attributes={
            "action.id": action.id,
            "span_id": action.span_id,
            "attempt": action.attempt_count,
        },
    )
