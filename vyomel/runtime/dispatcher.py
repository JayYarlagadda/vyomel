"""Dispatcher: Postgres commit, then Redis publish. Never the other way around.

docs/07-EXECUTION-ENGINE.md section 4.1. If the process dies between the two,
the action sits in DISPATCHED with a null lease and recovery republishes it.

Since M2 nothing reaches the queue without passing :class:`PolicyGate` first.
The gate is a constructor argument with no default: a dispatcher that can be
built without one is a dispatcher that will eventually be built without one.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from vyomel.core.clock import Clock, SystemClock
from vyomel.core.logging import get_logger
from vyomel.core.types import ActionStatus, Capability, TaskStatus
from vyomel.obs.tracing import format_traceparent
from vyomel.runtime.dag import ActionNode, ready_ids
from vyomel.runtime.gate import PolicyGate
from vyomel.runtime.queue import ActionQueue
from vyomel.runtime.state import ActionTrigger, TaskTrigger, apply_action, apply_task
from vyomel.security.audit import AuditEvent
from vyomel.store.models import Action, Step, Task
from vyomel.store.repos import ActionRepo, TaskRepo
from vyomel.tools.registry import ToolRegistry

log = get_logger(__name__)

_CONCURRENCY_LIMITS: dict[str, int] = {
    "desktop": 1,
    "browser": 1,
    "fs": 4,
    "http": 8,
    "model": 2,
}


class HostActionBridge(Protocol):
    async def try_dispatch(
        self,
        action_id: str,
        *,
        tool: str,
        parameters: dict[str, object],
        capability_level: Capability,
        traceparent: str | None = None,
    ) -> bool: ...


class Dispatcher:
    def __init__(
        self,
        queue: ActionQueue,
        registry: ToolRegistry,
        gate: PolicyGate,
        clock: Clock | None = None,
        *,
        max_parallel: int = 4,
    ) -> None:
        self._queue = queue
        self._registry = registry
        self._gate = gate
        self._clock = clock or SystemClock()
        self._max_parallel = max_parallel

    async def promote_ready(
        self, session: AsyncSession, actions: Sequence[Action], steps: Sequence[Step]
    ) -> int:
        """PLANNED → READY for actions whose dependencies are satisfied."""
        repo = ActionRepo(session)
        by_step = {step.id: step for step in steps}
        nodes = [
            ActionNode(
                id=action.id,
                status=action.status,
                depends_on=tuple(action.depends_on or ()),
                step_id=action.step_id,
                tolerates_unverified=by_step[action.step_id].tolerates_unverified,
            )
            for action in actions
        ]
        promoted = 0
        for action_id in ready_ids(nodes):
            dest = apply_action(ActionStatus.PLANNED, ActionTrigger.DEPENDENCIES_SATISFIED)
            row = await repo.cas_status(action_id, expected=ActionStatus.PLANNED, new=dest)
            if row is not None:
                promoted += 1
        return promoted

    async def enqueue_ready(
        self,
        session: AsyncSession,
        actions: Sequence[Action],
        steps: Sequence[Step],
        *,
        task: Task,
        now: datetime,
    ) -> list[tuple[str, str | None, str]]:
        """READY → DISPATCHED for as many gate-approved actions as parallelism allows.

        Returns ``(action_id, traceparent, tool)`` triples that must be published
        *after* the caller's transaction commits.
        """
        repo = ActionRepo(session)
        by_step = {step.id: step for step in steps}
        in_flight = [
            a for a in actions if a.status in (ActionStatus.DISPATCHED, ActionStatus.RUNNING)
        ]
        slots = max(0, self._max_parallel - len(in_flight))
        inflight_keys = _key_counts(in_flight, self._registry)
        published: list[tuple[str, str | None, str]] = []

        candidates = [
            a
            for a in actions
            if a.status is ActionStatus.READY and (a.available_at is None or a.available_at <= now)
        ]
        candidates.sort(key=lambda a: a.id)

        for action in candidates:
            if len(published) >= slots:
                break
            key = _concurrency_key(action.tool, self._registry)
            if key is not None:
                limit = _CONCURRENCY_LIMITS.get(key, self._max_parallel)
                if inflight_keys.get(key, 0) >= limit:
                    continue
            verdict = await self._gate.check(session, action, task, by_step.get(action.step_id))
            if not verdict.allowed:
                # The gate has already moved the action out of READY — to
                # WAITING_FOR_USER or FAILED. It does not occupy a slot.
                continue
            dest = apply_action(ActionStatus.READY, ActionTrigger.ENQUEUED)
            row = await repo.cas_status(
                action.id,
                expected=ActionStatus.READY,
                new=dest,
                dispatched_at=now,
                lease_owner=None,
                lease_until=None,
            )
            if row is None:
                continue
            await self._gate.audit.append(
                session,
                actor="dispatcher",
                event_type=AuditEvent.ACTION_DISPATCHED,
                task_id=task.id,
                action_id=action.id,
                capability_level=action.capability_level,
                payload={
                    "tool": action.tool,
                    "tool_version": action.tool_version,
                    "attempt": action.attempt_count,
                    "idempotency_key": action.idempotency_key,
                    "policy_rule_id": verdict.decision.rule_id if verdict.decision else None,
                    "approval_id": verdict.approval_id,
                },
            )
            published.append((action.id, format_traceparent(task.trace_id), action.tool))
            if key is not None:
                inflight_keys[key] = inflight_keys.get(key, 0) + 1
        return published

    async def publish(
        self,
        items: Sequence[tuple[str, str | None] | tuple[str, str | None, str] | str],
        *,
        host_bridge: HostActionBridge | None = None,
        action_params: dict[str, tuple[dict[str, object], object]] | None = None,
    ) -> None:
        """Publish to Redis, or to a local agent when the tool is host-bound.

        ``action_params`` maps action_id → (parameters, capability_level) for bridge dispatch.
        """
        for item in items:
            if isinstance(item, tuple) and len(item) == 3:
                action_id, traceparent, tool = item
            elif isinstance(item, tuple):
                action_id, traceparent = item[0], item[1]
                tool = ""
            else:
                action_id, traceparent, tool = item, None, ""

            routed = False
            if (
                host_bridge is not None
                and tool
                and _concurrency_key(tool, self._registry) == "desktop"
            ):
                params: dict[str, object] = {}
                level = None
                if action_params and action_id in action_params:
                    params, level = action_params[action_id]
                from vyomel.core.types import Capability

                cap = level if isinstance(level, Capability) else Capability.L1
                routed = await host_bridge.try_dispatch(
                    action_id,
                    tool=tool,
                    parameters=dict(params),
                    capability_level=cap,
                    traceparent=traceparent,
                )
            if not routed:
                await self._queue.publish(action_id, traceparent=traceparent)
            log.info(
                "vyomel.runtime.dispatched",
                action_id=action_id,
                tool=tool or None,
                via="local_agent" if routed else "redis",
            )

    async def maybe_start_task(
        self, session: AsyncSession, task: Task, *, now: datetime
    ) -> Task | None:
        """READY → RUNNING once the plan has actually begun to be acted on.

        Returns the updated task, because the caller's later decisions (block on
        an approval, complete, fail) all key off the *current* status.
        """
        if task.status is not TaskStatus.READY:
            return None
        dest = apply_task(TaskStatus.READY, TaskTrigger.FIRST_DISPATCH)
        return await TaskRepo(session).cas_status(
            task.id, expected=TaskStatus.READY, new=dest, started_at=now
        )


def _concurrency_key(tool_name: str, registry: ToolRegistry) -> str | None:
    if tool_name not in registry:
        return None
    return registry.get(tool_name).concurrency_key


def _key_counts(actions: Sequence[Action], registry: ToolRegistry) -> dict[str, int]:
    counts: dict[str, int] = {}
    for action in actions:
        key = _concurrency_key(action.tool, registry)
        if key is not None:
            counts[key] = counts.get(key, 0) + 1
    return counts
