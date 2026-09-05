"""Scheduler: reaper + DAG promotion + dispatch + task completion.

One tick is the unit tests drive. The long-running loop is a thin wrapper.
Startup recovery republishes DISPATCHED rows that Redis may have forgotten —
the hole §4.5 of the engine doc does not name, but write-ordering creates.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from vyomel.core.clock import Clock, SystemClock
from vyomel.core.config import Settings
from vyomel.core.logging import get_logger
from vyomel.core.types import ActionStatus, StepStatus, TaskStatus
from vyomel.runtime.dispatcher import Dispatcher
from vyomel.runtime.gate import PolicyGate
from vyomel.runtime.queue import ActionQueue
from vyomel.runtime.reaper import Reaper
from vyomel.runtime.replan_gate import NullReplanGate, ReplanGate
from vyomel.runtime.state import TaskTrigger, apply_task
from vyomel.security.policy import store_for
from vyomel.store.blobs import resolve_result
from vyomel.store.db import session_scope
from vyomel.store.models import Action, Step, Task
from vyomel.store.repos import ActionRepo, StepRepo, TaskRepo
from vyomel.tools.registry import ToolRegistry

log = get_logger(__name__)

# Statuses that mean "nothing has happened to this action yet".
_NOT_YET_STARTED = (ActionStatus.PLANNED, ActionStatus.READY)


class Scheduler:
    def __init__(
        self,
        settings: Settings,
        queue: ActionQueue,
        registry: ToolRegistry,
        clock: Clock | None = None,
        gate: PolicyGate | None = None,
        replan_gate: ReplanGate | None = None,
    ) -> None:
        self._settings = settings
        self._queue = queue
        self._clock = clock or SystemClock()
        self._reaper = Reaper(self._clock)
        self._gate = gate or PolicyGate(
            store_for(settings),
            approval_ttl_s=settings.approval_ttl_s,
            clock=self._clock,
        )
        self._replan_gate = replan_gate or NullReplanGate()
        self._dispatcher = Dispatcher(
            queue,
            registry,
            self._gate,
            self._clock,
            max_parallel=settings.max_parallel_actions,
        )
        self._stop = asyncio.Event()
        self._host_bridge = None

    def set_host_bridge(self, bridge: object | None) -> None:
        self._host_bridge = bridge

    @property
    def gate(self) -> PolicyGate:
        return self._gate

    async def recover(self) -> None:
        await self._queue.ensure_group()
        await self._reaper.reap()
        from vyomel.obs.tracing import format_traceparent

        republished = 0
        async with session_scope() as session:
            orphans = await ActionRepo(session).orphan_dispatched()
            payload: list[tuple[str, str | None, str]] = []
            params: dict[str, tuple[dict[str, object], object]] = {}
            for row in orphans:
                task = await TaskRepo(session).get(row.task_id)
                payload.append(
                    (row.id, format_traceparent(task.trace_id if task else None), row.tool)
                )
                params[row.id] = (dict(row.parameters or {}), row.capability_level)
        await self._dispatcher.publish(
            payload,
            host_bridge=self._host_bridge,  # type: ignore[arg-type]
            action_params=params,
        )
        republished = len(payload)
        claimed = await self._queue.autoclaim(
            "scheduler-recovery",
            min_idle_ms=self._settings.action_timeout_s * 1000,
        )
        log.info(
            "vyomel.runtime.recovered",
            republished=republished,
            autoclaimed=len(claimed),
        )
        await self.tick()

    async def tick(self) -> int:
        """One scheduler pass. Returns the number of actions published."""
        await self._reaper.reap()
        to_publish: list[tuple[str, str | None, str]] = []
        action_params: dict[str, tuple[dict[str, object], object]] = {}
        async with session_scope() as session:
            # Before anything is dispatched: unanswered approvals past their TTL
            # fail closed, so a stale gate cannot hold a slot indefinitely.
            await self._gate.expire_overdue(session)
            tasks = await TaskRepo(session).runnable()
            for task in tasks:
                batch = await self._tick_task(session, task)
                to_publish.extend(batch)
                if batch:
                    # Reload parameters for local-agent routing.
                    for action_id, _, tool in batch:
                        if tool.startswith("desktop."):
                            row = await ActionRepo(session).get(action_id)
                            if row is not None:
                                action_params[action_id] = (
                                    dict(row.parameters or {}),
                                    row.capability_level,
                                )
        await self._dispatcher.publish(
            to_publish,
            host_bridge=self._host_bridge,  # type: ignore[arg-type]
            action_params=action_params,
        )
        try:
            from vyomel.obs.metrics import QUEUE_DEPTH

            QUEUE_DEPTH.labels(stream=self._queue.stream).set(await self._queue.depth())
        except Exception:
            log.debug("vyomel.obs.queue_depth_failed", exc_info=True)
        return len(to_publish)

    async def run_forever(self) -> None:
        await self.recover()
        log.info("vyomel.runtime.scheduler_started")
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception:
                log.exception("vyomel.runtime.scheduler_tick_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=0.5)
            except TimeoutError:
                continue

    def stop(self) -> None:
        self._stop.set()

    async def _tick_task(
        self, session: AsyncSession, task: Task
    ) -> list[tuple[str, str | None, str]]:
        now = self._clock.now()
        if (
            task.deadline_at is not None
            and now >= task.deadline_at
            and task.status in (TaskStatus.READY, TaskStatus.RUNNING)
        ):
            dest = apply_task(task.status, TaskTrigger.DEADLINE)
            await TaskRepo(session).cas_status(
                task.id,
                expected=task.status,
                new=dest,
                finished_at=now,
                error={"code": "DEADLINE_EXCEEDED", "message": "task wall clock exceeded"},
            )
            return []

        action_repo = ActionRepo(session)
        step_repo = StepRepo(session)
        steps = await step_repo.list_by_task(task.id)
        actions = await action_repo.list_by_task(task.id)
        if not actions:
            return []

        task = await _resume_if_unblocked(session, task, actions) or task
        if task.status is TaskStatus.WAITING_FOR_USER:
            # Still gated. Promoting more actions would queue up approval
            # requests the user has not been given a chance to answer yet.
            return []

        await self._dispatcher.promote_ready(session, actions, steps)
        actions = await action_repo.list_by_task(task.id)
        published = await self._dispatcher.enqueue_ready(
            session, actions, steps, task=task, now=now
        )
        actions = await action_repo.list_by_task(task.id)
        if any(a.status not in _NOT_YET_STARTED for a in actions):
            # Dispatch is not the only way a plan starts. An action that the gate
            # sent to WAITING_FOR_USER — or denied outright — has begun to be
            # acted on, and a task left in READY would never reach a terminal
            # state, since both completion and failure are transitions out of
            # RUNNING.
            task = await self._dispatcher.maybe_start_task(session, task, now=now) or task
        await _sync_steps(step_repo, steps, actions)
        await _block_if_awaiting_user(session, task, actions)
        await _complete_if_done(
            session, task, actions, steps, now, self._settings, self._replan_gate
        )
        return published


async def _resume_if_unblocked(
    session: AsyncSession, task: Task, actions: list[Action]
) -> Task | None:
    """WAITING_FOR_USER → RUNNING once no action is still waiting on a human.

    The gate resumes the task itself when an approval is granted. This covers
    the other route out: the approval expired or was rejected, the action failed
    closed, and the task now has to reach a terminal state rather than sit in
    WAITING_FOR_USER forever.
    """
    if task.status is not TaskStatus.WAITING_FOR_USER:
        return None
    if any(a.status is ActionStatus.WAITING_FOR_USER for a in actions):
        return None
    dest = apply_task(TaskStatus.WAITING_FOR_USER, TaskTrigger.APPROVAL_GRANTED)
    return await TaskRepo(session).cas_status(
        task.id, expected=TaskStatus.WAITING_FOR_USER, new=dest
    )


async def _block_if_awaiting_user(session: AsyncSession, task: Task, actions: list[Action]) -> None:
    """RUNNING → WAITING_FOR_USER only when nothing else can proceed.

    Blocking the task while sibling actions are still in flight would take it
    out of the runnable set and strand them: the worker would finish the work
    and no tick would ever observe it.
    """
    if task.status is not TaskStatus.RUNNING:
        return
    live = [a for a in actions if not a.status.is_terminal]
    if not live or any(a.status is not ActionStatus.WAITING_FOR_USER for a in live):
        return
    dest = apply_task(TaskStatus.RUNNING, TaskTrigger.APPROVAL_NEEDED)
    await TaskRepo(session).cas_status(task.id, expected=TaskStatus.RUNNING, new=dest)


async def _sync_steps(step_repo: StepRepo, steps: list[Step], actions: list[Action]) -> None:
    by_step: dict[str, list[Action]] = {}
    for action in actions:
        by_step.setdefault(action.step_id, []).append(action)
    for step in steps:
        group = by_step.get(step.id, [])
        if not group:
            continue
        if any(not a.status.is_terminal for a in group):
            if any(a.status is ActionStatus.RUNNING for a in group):
                await step_repo.set_status(step.id, StepStatus.RUNNING)
            continue
        if any(a.status is ActionStatus.FAILED for a in group):
            await step_repo.set_status(step.id, StepStatus.FAILED)
        elif all(_action_satisfied(a, step) for a in group):
            await step_repo.set_status(step.id, StepStatus.SUCCEEDED)
        elif all(a.status.is_terminal for a in group):
            # UNVERIFIED without an opt-in is not a success. The step fails so
            # the task cannot report SUCCEEDED over an unproven action.
            await step_repo.set_status(step.id, StepStatus.FAILED)


def _action_satisfied(action: Action, step: Step) -> bool:
    """Succeeded, or UNVERIFIED on a step that opted in (07 §5)."""
    if action.status is ActionStatus.SUCCEEDED:
        return True
    return action.status is ActionStatus.UNVERIFIED and step.tolerates_unverified


async def _complete_if_done(
    session: AsyncSession,
    task: Task,
    actions: list[Action],
    steps: list[Step],
    now: datetime,
    settings: Settings,
    replan_gate: ReplanGate,
) -> None:
    if any(not a.status.is_terminal for a in actions):
        return
    if task.status is not TaskStatus.RUNNING:
        return

    if any(a.status is ActionStatus.FAILED for a in actions):
        if task.replan_count < settings.max_replans:
            replanned = await replan_gate.try_replan(session, task, actions, steps)
            if replanned:
                from vyomel.obs.metrics import TASK_REPLANS

                TASK_REPLANS.inc()
                return
        trigger = (
            TaskTrigger.REPLAN_EXHAUSTED
            if task.replan_count >= settings.max_replans
            else TaskTrigger.REQUIRED_FAILED
        )
        dest = apply_task(task.status, trigger)
        await TaskRepo(session).cas_status(
            task.id,
            expected=task.status,
            new=dest,
            finished_at=now,
            error={"code": "ACTION_FAILED", "message": "a required action failed"},
        )
        _record_task_finished(task, dest, now, len(steps))
        return

    by_step = {s.id: s for s in steps}
    unverified = [
        a
        for a in actions
        if a.status is ActionStatus.UNVERIFIED and not by_step[a.step_id].tolerates_unverified
    ]
    if unverified:
        dest = apply_task(task.status, TaskTrigger.REQUIRED_FAILED)
        await TaskRepo(session).cas_status(
            task.id,
            expected=task.status,
            new=dest,
            finished_at=now,
            error={
                "code": "UNVERIFIED",
                "message": "an action finished UNVERIFIED and the step does not tolerate it",
            },
        )
        _record_task_finished(task, dest, now, len(steps))
        return

    reports = [
        resolve_result(a.result, blob_dir=settings.blob_dir)
        for a in actions
        if a.tool == "task.report" and a.status is ActionStatus.SUCCEEDED and a.result
    ]
    dest = apply_task(task.status, TaskTrigger.ALL_SUCCEEDED)
    await TaskRepo(session).cas_status(
        task.id,
        expected=task.status,
        new=dest,
        finished_at=now,
        result=reports[-1] if reports else {"actions_succeeded": len(actions)},
    )
    from vyomel.memory.episodes import record_episode

    task.status = dest
    task.finished_at = now
    _record_task_finished(task, dest, now, len(steps))
    await record_episode(session, task=task, actions=actions, settings=settings)


def _record_task_finished(task: Task, status: TaskStatus, now: datetime, step_count: int) -> None:
    from vyomel.obs.metrics import TASK_COST, TASK_DURATION, TASK_STEPS, TASKS_TOTAL

    TASKS_TOTAL.labels(status=status.value, origin=task.origin.value).inc()
    start = task.started_at or task.created_at
    if start is not None:
        TASK_DURATION.labels(status=status.value).observe((now - start).total_seconds())
    TASK_STEPS.observe(step_count)
    TASK_COST.observe(float(task.cost_usd or 0))
