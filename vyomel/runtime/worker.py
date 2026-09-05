"""Worker: claim a lease, invoke a tool, persist the outcome.

Claim commits before the tool runs. A crash mid-execute leaves a RUNNING row
with an expiry the reaper will reclaim; idempotent tools (all of M1) are safe
to replay. Non-idempotent tools reserve the side-effect ledger first.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from vyomel.core.cancel import CancellationToken
from vyomel.core.clock import Clock, SystemClock
from vyomel.core.config import Settings
from vyomel.core.errors import ErrorCode, ToolError, VyomelError
from vyomel.core.logging import bind_task_context, clear_task_context, get_logger
from vyomel.core.types import ActionStatus, TaskStatus, VerifyOutcome
from vyomel.obs.metrics import (
    ACTION_DURATION,
    ACTION_RETRIES,
    ACTIONS_TOTAL,
    ACTUATION_TIER,
    DEAD_LETTERS,
    QUEUE_WAIT,
    TOOL_ERRORS,
    UNVERIFIED_ACTIONS,
    VERIFICATION_DURATION,
    VERIFICATIONS,
    WORKERS_ACTIVE,
)
from vyomel.obs.tracing import SpanLink, parse_traceparent, start_span
from vyomel.runtime.queue import ActionQueue, StreamMessage
from vyomel.runtime.retry import delay_s
from vyomel.runtime.state import ActionTrigger, apply_action
from vyomel.security.audit import AuditEvent, AuditTrail
from vyomel.store.blobs import spill_if_large
from vyomel.store.db import session_scope
from vyomel.store.models import Action
from vyomel.store.repos import ActionRepo, TaskRepo, VerificationRepo
from vyomel.tools.base import ToolContext
from vyomel.tools.registry import RegistryError, ToolRegistry
from vyomel.verify.engine import ObserveContext, VerificationReport, verify_result

log = get_logger(__name__)


class Worker:
    def __init__(
        self,
        settings: Settings,
        queue: ActionQueue,
        registry: ToolRegistry,
        *,
        worker_id: str,
        clock: Clock | None = None,
        cancel: CancellationToken | None = None,
        audit: AuditTrail | None = None,
    ) -> None:
        self._settings = settings
        self._queue = queue
        self._registry = registry
        self._worker_id = worker_id
        self._clock = clock or SystemClock()
        self._cancel = cancel or CancellationToken()
        self._audit = audit or AuditTrail(self._clock)

    async def run_once(self, *, block_ms: int = 1_000) -> bool:
        """Claim and handle at most one message. Returns True if a message was seen."""
        message = await self._queue.claim(self._worker_id, block_ms=block_ms)
        if message is None:
            return False
        ack = False
        try:
            ack = await self._handle(message)
        except Exception:
            log.exception("vyomel.runtime.worker_crash", action_id=message.action_id)
            ack = False
        if ack:
            await self._queue.ack(message)
        return True

    async def run_forever(self) -> None:
        await self._queue.ensure_group()
        WORKERS_ACTIVE.inc()
        log.info("vyomel.runtime.worker_started", worker_id=self._worker_id)
        try:
            while not self._cancel.cancelled:
                await self.run_once(block_ms=5_000)
        finally:
            WORKERS_ACTIVE.dec()

    async def _handle(self, message: StreamMessage) -> bool:
        now = self._clock.now()
        async with session_scope() as session:
            repo = ActionRepo(session)
            existing = await repo.get(message.action_id)
            if existing is None:
                return True
            previous_span_id = existing.span_id
            timeout_s = existing.timeout_s
            action = await repo.cas_claim(
                message.action_id,
                worker_id=self._worker_id,
                lease_until=now + timedelta(seconds=timeout_s),
                now=now,
            )
            if action is None:
                return True
            task_row = await TaskRepo(session).get(action.task_id)
            trace_id = None
            parent_span = None
            parsed = parse_traceparent(message.traceparent)
            if parsed is not None:
                trace_id, parent_span = parsed
            elif task_row is not None and task_row.trace_id:
                trace_id = task_row.trace_id
        await self._execute(
            message.action_id,
            trace_id=trace_id,
            parent_span_id=parent_span,
            previous_span_id=previous_span_id,
        )
        return True

    async def _execute(
        self,
        action_id: str,
        *,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        previous_span_id: str | None = None,
    ) -> None:
        async with session_scope() as session:
            repo = ActionRepo(session)
            action = await repo.get(action_id)
            if action is None or action.status is not ActionStatus.RUNNING:
                return
            task = await TaskRepo(session).get(action.task_id)
            if task is not None and task.status is TaskStatus.CANCELLED:
                await _finish_cancelled(repo, action, self._clock.now(), session=session)
                await session.refresh(action)
                await self._record_outcome(session, action)
                return
            links: list[SpanLink] = []
            if previous_span_id and action.attempt_count > 1 and trace_id:
                crash = action.error is None or (action.error or {}).get("code") == "TIMEOUT"
                links.append(
                    SpanLink(
                        trace_id,
                        previous_span_id,
                        {"resumed_after_crash": crash},
                    )
                )
            bind_task_context(task_id=action.task_id, action_id=action.id, trace_id=trace_id)
            if action.dispatched_at is not None:
                QUEUE_WAIT.observe((self._clock.now() - action.dispatched_at).total_seconds())
            try:
                with start_span(
                    "action",
                    trace_id=trace_id,
                    parent_span_id=parent_span_id,
                    links=links,
                ) as span:
                    span.set(
                        **{
                            "action.id": action.id,
                            "action.tool": action.tool,
                            "action.tool_version": action.tool_version,
                            "action.capability": action.capability_level.value,
                            "action.attempt": action.attempt_count,
                        }
                    )
                    bind_task_context(span_id=span.span_id, trace_id=span.trace_id)
                    async with session_scope() as stamp:
                        stamped = await ActionRepo(stamp).get(action.id)
                        if stamped is not None:
                            stamped.span_id = span.span_id
                    await _run_claimed(
                        action,
                        session=session,
                        repo=repo,
                        registry=self._registry,
                        settings=self._settings,
                        clock=self._clock,
                        cancel=self._cancel,
                        audit=self._audit,
                        actor=f"worker:{self._worker_id}",
                        worker_id=self._worker_id,
                    )
                    await session.refresh(action)
                    span.set(**{"action.status": action.status.value})
            finally:
                clear_task_context()
            await self._record_outcome(session, action)
            _record_action_metrics(action)

    async def _record_outcome(self, session: AsyncSession, action: Action) -> None:
        """One audit record per execution attempt, whatever the outcome.

        Written here rather than at each of ``_run_claimed``'s exits: a dozen
        call sites is a dozen chances to add a thirteenth that forgets. The
        result itself is summarized, not copied â€” audit payloads are permanent,
        and a tool result can be a megabyte of file content.
        """
        await self._audit.append(
            session,
            actor=f"worker:{self._worker_id}",
            event_type=AuditEvent.ACTION_FINISHED,
            task_id=action.task_id,
            action_id=action.id,
            capability_level=action.capability_level,
            payload={
                "tool": action.tool,
                "status": action.status.value,
                "attempt": action.attempt_count,
                "result_fields": sorted(action.result or {}),
                "error": action.error,
            },
        )


async def _run_claimed(
    action: Action,
    *,
    session: AsyncSession,
    repo: ActionRepo,
    registry: ToolRegistry,
    settings: Settings,
    clock: Clock,
    cancel: CancellationToken,
    audit: AuditTrail,
    actor: str,
    worker_id: str,
) -> None:
    now = clock.now()
    try:
        tool = registry.get(action.tool)
    except RegistryError as exc:
        await _finish_failed(
            repo, action, now, code=ErrorCode.UNSUPPORTED, message=str(exc), retryable=False
        )
        return

    try:
        params = tool.Input.model_validate(action.parameters)
    except ValidationError as exc:
        await _finish_failed(
            repo, action, now, code=ErrorCode.INVALID_PARAMETERS, message=str(exc), retryable=False
        )
        return

    action_cancel = CancellationToken()
    ctx = ToolContext(
        task_id=action.task_id,
        action_id=action.id,
        capability_granted=action.capability_level,
        scratch_dir=settings.scratch_dir,
        allowed_roots=list(settings.allowed_roots),
        deadline=action.lease_until or now + timedelta(seconds=action.timeout_s),
        cancel=action_cancel,
        clock=clock,
        trash_dir=settings.trash_dir,
        settings=settings,
    )
    pre = await tool.preflight(params, ctx)
    if not pre.ok:
        await _finish_failed(
            repo,
            action,
            now,
            code=ErrorCode.PRECONDITION_FAILED,
            message=pre.reason or "preflight failed",
            retryable=False,
        )
        return

    if not tool.idempotent:
        reserved = await repo.insert_ledger(
            key=action.idempotency_key, tool=tool.name, action_id=action.id
        )
        if not reserved:
            if action.result is not None:
                dest = apply_action(ActionStatus.RUNNING, ActionTrigger.VERIFICATION_PASS)
                await repo.cas_status(
                    action.id, expected=ActionStatus.RUNNING, new=dest, finished_at=now
                )
                return
            await _finish_failed(
                repo,
                action,
                now,
                code=ErrorCode.CONFLICT,
                message="side-effect reserved but no result; refusing to replay",
                retryable=False,
            )
            return

    execute_task = asyncio.create_task(tool.execute(params, ctx))
    watch_task = asyncio.create_task(
        _watch_for_cancel(
            task_id=action.task_id,
            token=action_cancel,
            execute_task=execute_task,
            grace_s=settings.cancel_grace_s,
            clock=clock,
            worker_cancel=cancel,
        )
    )
    heartbeat_stop = asyncio.Event()
    heartbeat_task: asyncio.Task[None] | None = None
    if settings.heartbeat_interval_s > 0:
        heartbeat_task = asyncio.create_task(
            _heartbeat_loop(
                action_id=action.id,
                worker_id=worker_id,
                timeout_s=action.timeout_s,
                interval_s=settings.heartbeat_interval_s,
                clock=clock,
                stop=heartbeat_stop,
            )
        )
    try:
        with start_span("tool.execute") as tool_span:
            tool_span.set(**{"tool.name": tool.name, "tool.tier": tool.actuation_tier})
            output = await asyncio.wait_for(execute_task, timeout=action.timeout_s)
            tier = getattr(output, "actuation_tier", None)
            if tier is None:
                tier = tool.actuation_tier
            ACTUATION_TIER.labels(tier=str(tier)).inc()
            tool_span.set(**{"tool.tier": tier})
    except TimeoutError:
        await _retry_or_fail(
            repo, action, now, code=ErrorCode.TIMEOUT, message="action timed out", retryable=True
        )
        return
    except asyncio.CancelledError:
        if action_cancel.cancelled or cancel.cancelled:
            await _finish_cancelled(repo, action, clock.now(), session=session)
            return
        raise
    except ToolError as exc:
        if action_cancel.cancelled:
            await _finish_cancelled(repo, action, clock.now(), session=session)
            return
        await _retry_or_fail(
            repo,
            action,
            now,
            code=exc.code,
            message=exc.user_message,
            retryable=exc.retryable,
            observation=exc.observation,
        )
        return
    except VyomelError as exc:
        if action_cancel.cancelled:
            await _finish_cancelled(repo, action, clock.now(), session=session)
            return
        await _retry_or_fail(
            repo, action, now, code=exc.code, message=exc.user_message, retryable=exc.retryable
        )
        return
    except Exception as exc:
        log.exception("vyomel.runtime.tool_uncaught", action_id=action.id, tool=action.tool)
        await _finish_failed(
            repo,
            action,
            now,
            code=ErrorCode.INTERNAL,
            message="tool raised an unexpected error",
            retryable=False,
            observation=type(exc).__name__,
        )
        return
    finally:
        heartbeat_stop.set()
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
        watch_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watch_task

    result = output.model_dump(mode="json")
    stored = spill_if_large(
        result,
        blob_dir=settings.blob_dir,
        threshold=settings.blob_spill_threshold_bytes,
    )
    postconditions = list(action.postconditions or [])
    if not postconditions:
        postconditions = tool.verification_plan(params, output)
    report = verify_result(
        capability=action.capability_level,
        postconditions=postconditions,
        result=result,
        allowed_roots=list(settings.allowed_roots),
        observe=ObserveContext(task_id=action.task_id, settings=settings),
    )
    with start_span("verify") as verify_span:
        verify_span.set(
            **{
                "verify.outcome": report.outcome.value,
                "verify.type": report.verifier,
            }
        )
    for check in report.checks:
        VERIFICATIONS.labels(type=check.verifier, outcome=check.outcome.value).inc()
        VERIFICATION_DURATION.labels(type=check.verifier).observe(check.latency_ms / 1000.0)
    await VerificationRepo(session).record(action.id, report.checks)
    await _audit_verification(audit, session, action, report, actor=actor)
    if report.outcome is VerifyOutcome.PASS:
        dest = apply_action(ActionStatus.RUNNING, ActionTrigger.VERIFICATION_PASS)
        await repo.cas_status(
            action.id,
            expected=ActionStatus.RUNNING,
            new=dest,
            result=stored,
            finished_at=clock.now(),
            lease_owner=None,
            lease_until=None,
            error=None,
        )
        return
    if report.outcome is VerifyOutcome.NO_METHOD:
        dest = apply_action(ActionStatus.RUNNING, ActionTrigger.VERIFICATION_NO_METHOD)
        await repo.cas_status(
            action.id,
            expected=ActionStatus.RUNNING,
            new=dest,
            result=stored,
            finished_at=clock.now(),
            lease_owner=None,
            lease_until=None,
        )
        return
    await _finish_failed(
        repo,
        action,
        clock.now(),
        code=ErrorCode.VERIFICATION_FAILED,
        message="postcondition failed",
        retryable=False,
        result=stored,
        observation=_failing_observation(report),
    )


async def _retry_or_fail(
    repo: ActionRepo,
    action: Action,
    now: Any,
    *,
    code: ErrorCode,
    message: str,
    retryable: bool,
    observation: str | None = None,
) -> None:
    error = {
        "code": code.value,
        "message": message,
        "retryable": retryable,
        "observation": observation,
    }
    if retryable and action.attempt_count < action.max_retries:
        dest = apply_action(ActionStatus.RUNNING, ActionTrigger.TOOL_FAILED_RETRYABLE)
        wait = delay_s(action.attempt_count)
        await repo.cas_status(
            action.id,
            expected=ActionStatus.RUNNING,
            new=dest,
            error=error,
            available_at=now + timedelta(seconds=wait),
            lease_owner=None,
            lease_until=None,
        )
        log.info("vyomel.runtime.retry_scheduled", action_id=action.id, delay_s=round(wait, 3))
        return
    await _finish_failed(
        repo, action, now, code=code, message=message, retryable=retryable, observation=observation
    )


async def _finish_failed(
    repo: ActionRepo,
    action: Action,
    now: Any,
    *,
    code: ErrorCode,
    message: str,
    retryable: bool,
    observation: str | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    dest = apply_action(ActionStatus.RUNNING, ActionTrigger.TOOL_FAILED_TERMINAL)
    fields: dict[str, Any] = {
        "error": {
            "code": code.value,
            "message": message,
            "retryable": retryable,
            "observation": observation,
        },
        "finished_at": now,
        "lease_owner": None,
        "lease_until": None,
    }
    if result is not None:
        fields["result"] = result
    await repo.cas_status(action.id, expected=ActionStatus.RUNNING, new=dest, **fields)
    await repo.add_dead_letter(
        action_id=action.id,
        reason=code.value,
        context={"message": message, "attempt_count": action.attempt_count},
    )
    DEAD_LETTERS.labels(tool=action.tool).inc()
    log.info("vyomel.runtime.action_failed", action_id=action.id, code=code.value)


async def _finish_cancelled(
    repo: ActionRepo, action: Action, now: Any, *, session: AsyncSession
) -> None:
    dest = apply_action(ActionStatus.RUNNING, ActionTrigger.TASK_CANCELLED)
    moved = await repo.cas_status(
        action.id,
        expected=ActionStatus.RUNNING,
        new=dest,
        finished_at=now,
        lease_owner=None,
        lease_until=None,
    )
    if moved is None:
        return
    await session.commit()
    log.info("vyomel.runtime.action_cancelled", action_id=action.id)


async def _heartbeat_loop(
    *,
    action_id: str,
    worker_id: str,
    timeout_s: int,
    interval_s: float,
    clock: Clock,
    stop: asyncio.Event,
) -> None:
    """Extend the action lease while the tool is still executing."""
    while not stop.is_set():
        await asyncio.sleep(interval_s)
        if stop.is_set():
            return
        now = clock.now()
        async with session_scope() as session:
            await ActionRepo(session).extend_lease(
                action_id,
                worker_id=worker_id,
                lease_until=now + timedelta(seconds=timeout_s),
            )


async def _watch_for_cancel(
    *,
    task_id: str,
    token: CancellationToken,
    execute_task: asyncio.Task[Any],
    grace_s: float,
    clock: Clock,
    worker_cancel: CancellationToken,
) -> None:
    """Set the per-action token when the task is cancelled; after grace, abort execute.

    Coordination is through Postgres (the task row). Workers share no in-memory
    state, so this loop is how a canceller in another process is observed.
    """
    signalled_at = None
    poll = 0.05 if grace_s <= 0 else min(0.1, max(0.02, grace_s / 5))
    while not execute_task.done():
        if worker_cancel.cancelled:
            token.cancel()
            execute_task.cancel()
            return
        async with session_scope() as session:
            task = await TaskRepo(session).get(task_id)
        if task is not None and task.status is TaskStatus.CANCELLED:
            token.cancel()
            if signalled_at is None:
                signalled_at = clock.now()
            elapsed = (clock.now() - signalled_at).total_seconds()
            if elapsed >= grace_s:
                execute_task.cancel()
                return
        await asyncio.sleep(poll)


async def _audit_verification(
    audit: AuditTrail,
    session: AsyncSession,
    action: Action,
    report: VerificationReport,
    *,
    actor: str,
) -> None:
    await audit.append(
        session,
        actor=actor,
        event_type=AuditEvent.VERIFICATION_COMPLETED,
        task_id=action.task_id,
        action_id=action.id,
        capability_level=action.capability_level,
        payload={
            "outcome": report.outcome.value,
            "checks": [
                {
                    "verifier": check.verifier,
                    "outcome": check.outcome.value,
                    "expected": _trim(check.expected),
                    "observed": _trim(check.observed),
                    "evidence_ref": check.evidence_ref,
                    "latency_ms": check.latency_ms,
                }
                for check in report.checks
            ],
        },
    )


def _record_action_metrics(action: Action) -> None:
    ACTIONS_TOTAL.labels(
        tool=action.tool,
        status=action.status.value,
        capability=action.capability_level.value,
    ).inc()
    if action.started_at and action.finished_at:
        ACTION_DURATION.labels(tool=action.tool).observe(
            (action.finished_at - action.started_at).total_seconds()
        )
    if action.status is ActionStatus.UNVERIFIED:
        UNVERIFIED_ACTIONS.labels(tool=action.tool).inc()
    error = action.error or {}
    code = error.get("code")
    if code:
        retryable = "true" if error.get("retryable") else "false"
        TOOL_ERRORS.labels(tool=action.tool, code=str(code), retryable=retryable).inc()
        if error.get("retryable"):
            ACTION_RETRIES.labels(tool=action.tool, error_code=str(code)).inc()


def _failing_observation(report: VerificationReport) -> str:
    failed = [c for c in report.checks if c.outcome is VerifyOutcome.FAIL]
    if not failed:
        return report.outcome.value
    first = failed[0]
    return f"{first.verifier}: expected {first.expected!r}, observed {first.observed!r}"


def _trim(value: Any, *, limit: int = 512) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "â€¦"
    return value
