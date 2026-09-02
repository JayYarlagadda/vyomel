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

from astra.core.cancel import CancellationToken
from astra.core.clock import Clock, SystemClock
from astra.core.config import Settings
from astra.core.errors import AstraError, ErrorCode, ToolError
from astra.core.logging import get_logger
from astra.core.types import ActionStatus, TaskStatus, VerifyOutcome
from astra.runtime.queue import ActionQueue, StreamMessage
from astra.runtime.retry import delay_s
from astra.runtime.state import ActionTrigger, apply_action
from astra.security.audit import AuditEvent, AuditTrail
from astra.store.db import session_scope
from astra.store.models import Action
from astra.store.repos import ActionRepo, TaskRepo, VerificationRepo
from astra.tools.base import ToolContext
from astra.tools.registry import RegistryError, ToolRegistry
from astra.verify.engine import VerificationReport, verify_result

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
            log.exception("astra.runtime.worker_crash", action_id=message.action_id)
            ack = False
        if ack:
            await self._queue.ack(message)
        return True

    async def run_forever(self) -> None:
        await self._queue.ensure_group()
        log.info("astra.runtime.worker_started", worker_id=self._worker_id)
        while not self._cancel.cancelled:
            await self.run_once(block_ms=5_000)

    async def _handle(self, message: StreamMessage) -> bool:
        now = self._clock.now()
        async with session_scope() as session:
            repo = ActionRepo(session)
            existing = await repo.get(message.action_id)
            if existing is None:
                return True
            timeout_s = existing.timeout_s
            action = await repo.cas_claim(
                message.action_id,
                worker_id=self._worker_id,
                lease_until=now + timedelta(seconds=timeout_s),
                now=now,
            )
            if action is None:
                return True
        await self._execute(message.action_id)
        return True

    async def _execute(self, action_id: str) -> None:
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
            )
            await session.refresh(action)
            await self._record_outcome(session, action)

    async def _record_outcome(self, session: AsyncSession, action: Action) -> None:
        """One audit record per execution attempt, whatever the outcome.

        Written here rather than at each of ``_run_claimed``'s exits: a dozen
        call sites is a dozen chances to add a thirteenth that forgets. The
        result itself is summarized, not copied — audit payloads are permanent,
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
    try:
        output = await asyncio.wait_for(execute_task, timeout=action.timeout_s)
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
    except AstraError as exc:
        if action_cancel.cancelled:
            await _finish_cancelled(repo, action, clock.now(), session=session)
            return
        await _retry_or_fail(
            repo, action, now, code=exc.code, message=exc.user_message, retryable=exc.retryable
        )
        return
    except Exception as exc:
        log.exception("astra.runtime.tool_uncaught", action_id=action.id, tool=action.tool)
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
        watch_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watch_task

    result = output.model_dump(mode="json")
    postconditions = list(action.postconditions or [])
    if not postconditions:
        postconditions = tool.verification_plan(params, output)
    report = verify_result(
        capability=action.capability_level,
        postconditions=postconditions,
        result=result,
        allowed_roots=list(settings.allowed_roots),
    )
    await VerificationRepo(session).record(action.id, report.checks)
    await _audit_verification(audit, session, action, report, actor=actor)
    if report.outcome is VerifyOutcome.PASS:
        dest = apply_action(ActionStatus.RUNNING, ActionTrigger.VERIFICATION_PASS)
        await repo.cas_status(
            action.id,
            expected=ActionStatus.RUNNING,
            new=dest,
            result=result,
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
            result=result,
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
        result=result,
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
        log.info("astra.runtime.retry_scheduled", action_id=action.id, delay_s=round(wait, 3))
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
    log.info("astra.runtime.action_failed", action_id=action.id, code=code.value)


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
    log.info("astra.runtime.action_cancelled", action_id=action.id)


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


def _failing_observation(report: VerificationReport) -> str:
    failed = [c for c in report.checks if c.outcome is VerifyOutcome.FAIL]
    if not failed:
        return report.outcome.value
    first = failed[0]
    return f"{first.verifier}: expected {first.expected!r}, observed {first.observed!r}"


def _trim(value: Any, *, limit: int = 512) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    return value
