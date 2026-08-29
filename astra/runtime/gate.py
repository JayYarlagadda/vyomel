"""The gate between READY and DISPATCHED (FR-302, FR-303, FR-305).

M1's dispatcher published any action whose dependencies were satisfied. This is
the component the roadmap said M2 would insert in that seam: policy is
evaluated for every action, every decision is audited, and an action needing
human consent stops in ``WAITING_FOR_USER`` instead of running.

It lives in the runtime rather than in ``astra.security`` because it is the one
place that needs *both* the approval records and the action state machine, and
the layering table lets the runtime import the security layer but not the
reverse. ``astra.security`` stays free of scheduler concepts as a result.

Fail-closed points, all of which are load-bearing:

- An action classified above its task's capability ceiling fails. The ceiling is
  the user's up-front consent boundary and nothing discovered mid-task may raise
  it (docs/06 section 5).
- A ``DENY`` verdict fails the action and dead-letters it. There is no path from
  ``DENY`` to a retry, because retrying a denied action is just a slower denial.
- An expired approval fails the action. Silence is never consent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from astra.core.clock import Clock, SystemClock
from astra.core.errors import ErrorCode
from astra.core.logging import get_logger
from astra.core.types import ActionStatus, Capability, Decision, TaskStatus
from astra.runtime.state import ActionTrigger, TaskTrigger, apply_action, apply_task
from astra.security.approvals import ApprovalRequest, ApprovalService, parameter_hash
from astra.security.audit import AuditEvent, AuditTrail
from astra.security.policy import PolicyDecision, PolicyRequest, PolicyStore
from astra.store.models import Action, Step, Task
from astra.store.repos import ActionRepo, TaskRepo

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class GateVerdict:
    allowed: bool
    reason: str
    decision: PolicyDecision | None = None
    approval_id: str | None = None


class PolicyGate:
    def __init__(
        self,
        policy_store: PolicyStore,
        *,
        approval_ttl_s: int,
        clock: Clock | None = None,
        audit: AuditTrail | None = None,
    ) -> None:
        self._policies = policy_store
        self._ttl_s = approval_ttl_s
        self._clock = clock or SystemClock()
        self._audit = audit or AuditTrail(self._clock)

    async def check(
        self,
        session: AsyncSession,
        action: Action,
        task: Task,
        step: Step | None = None,
    ) -> GateVerdict:
        """Decide whether this action may be dispatched now.

        Any outcome other than ``allowed`` has already moved the action out of
        ``READY``, so the caller must not dispatch it.
        """
        if action.capability_level > task.capability_ceiling:
            return await self._refuse_ceiling(session, action, task)

        policy = self._policies.get()
        request = PolicyRequest(
            tool=action.tool,
            level=action.capability_level,
            parameters=dict(action.parameters),
            workflow=_workflow_of(task),
        )
        decision = policy.evaluate(request)

        if decision.decision is Decision.DENY:
            return await self._refuse_policy(session, action, task, decision)
        if decision.decision is Decision.ALLOW:
            await self._audit.append(
                session,
                actor="policy",
                event_type=AuditEvent.POLICY_ALLOWED,
                task_id=task.id,
                action_id=action.id,
                capability_level=action.capability_level,
                payload={"tool": action.tool, **decision.to_payload()},
            )
            return GateVerdict(allowed=True, reason=decision.reason, decision=decision)
        return await self._require_approval(session, action, task, step, decision)

    async def grant(
        self,
        session: AsyncSession,
        action: Action,
        *,
        approval_id: str,
        decided_by: str,
        modified: bool = False,
    ) -> None:
        """WAITING_FOR_USER → READY after a positive decision.

        The action goes back to READY rather than straight to DISPATCHED, so the
        next tick re-evaluates policy against whatever the row now says. An
        approval does not bypass the gate; it satisfies it.
        """
        dest = apply_action(ActionStatus.WAITING_FOR_USER, ActionTrigger.APPROVAL_GRANTED)
        await ActionRepo(session).cas_status(
            action.id,
            expected=ActionStatus.WAITING_FOR_USER,
            new=dest,
            available_at=None,
            error=None,
        )
        await self._audit.append(
            session,
            actor=f"user:{decided_by}",
            event_type=(AuditEvent.APPROVAL_MODIFIED if modified else AuditEvent.APPROVAL_GRANTED),
            task_id=action.task_id,
            action_id=action.id,
            capability_level=action.capability_level,
            payload={
                "approval_id": approval_id,
                "tool": action.tool,
                "parameters": dict(action.parameters),
            },
        )
        await self._resume_task(session, action.task_id)

    async def refuse(
        self,
        session: AsyncSession,
        action: Action,
        *,
        approval_id: str,
        decided_by: str,
        event_type: str,
        code: ErrorCode = ErrorCode.PERMISSION_DENIED,
        message: str = "approval rejected",
    ) -> None:
        """WAITING_FOR_USER → FAILED, for both rejection and expiry."""
        now = self._clock.now()
        dest = apply_action(ActionStatus.WAITING_FOR_USER, ActionTrigger.APPROVAL_REJECTED)
        await ActionRepo(session).cas_status(
            action.id,
            expected=ActionStatus.WAITING_FOR_USER,
            new=dest,
            finished_at=now,
            lease_owner=None,
            lease_until=None,
            error={
                "code": code.value,
                "message": message,
                "retryable": False,
                "observation": None,
            },
        )
        await ActionRepo(session).add_dead_letter(
            action_id=action.id, reason=code.value, context={"message": message}
        )
        await self._audit.append(
            session,
            actor=f"user:{decided_by}" if decided_by else "policy",
            event_type=event_type,
            task_id=action.task_id,
            action_id=action.id,
            capability_level=action.capability_level,
            payload={"approval_id": approval_id, "tool": action.tool, "message": message},
        )
        await self._fail_task_if_blocked(session, action.task_id, message)

    async def expire_overdue(
        self, session: AsyncSession, *, now: datetime | None = None
    ) -> list[str]:
        """Sweep unanswered approvals. Called from the scheduler tick (FR-305)."""
        moment = now or self._clock.now()
        service = self._service(session)
        expired: list[str] = []
        for approval in await service.due_for_expiry(moment):
            await service.mark_expired(approval)
            action = await ActionRepo(session).get(approval.action_id)
            if action is not None and action.status is ActionStatus.WAITING_FOR_USER:
                await self.refuse(
                    session,
                    action,
                    approval_id=approval.id,
                    decided_by="",
                    event_type=AuditEvent.APPROVAL_EXPIRED,
                    message=(
                        f"approval expired unanswered after {self._ttl_s}s; failing closed (FR-305)"
                    ),
                )
            expired.append(approval.id)
        return expired

    def service(self, session: AsyncSession) -> ApprovalService:
        return self._service(session)

    @property
    def audit(self) -> AuditTrail:
        return self._audit

    # ----------------------------------------------------------------- private

    def _service(self, session: AsyncSession) -> ApprovalService:
        return ApprovalService(session, ttl_s=self._ttl_s, clock=self._clock)

    async def _require_approval(
        self,
        session: AsyncSession,
        action: Action,
        task: Task,
        step: Step | None,
        decision: PolicyDecision,
    ) -> GateVerdict:
        service = self._service(session)
        params_hash = parameter_hash(dict(action.parameters))

        usable = await service.usable_for(action.id, params_hash, action.capability_level)
        if usable is not None:
            await service.consume(usable)
            await self._audit.append(
                session,
                actor="policy",
                event_type=AuditEvent.APPROVAL_CONSUMED,
                task_id=task.id,
                action_id=action.id,
                capability_level=action.capability_level,
                payload={"approval_id": usable.id, **decision.to_payload()},
            )
            return GateVerdict(
                allowed=True,
                reason="covered by a decided approval",
                decision=decision,
                approval_id=usable.id,
            )

        existing = await service.pending_for(action.id)
        if existing is None:
            approval = await service.create(
                ApprovalRequest(
                    action_id=action.id,
                    task_id=task.id,
                    capability_level=action.capability_level,
                    summary=_summarize(action, step),
                    presented=_presented(action, step, decision, task),
                    blast_radius=_blast_radius(action),
                    parameter_hash=params_hash,
                    policy_rule_id=decision.rule_id,
                    policy_hash=decision.policy_hash,
                )
            )
        else:
            approval = existing

        dest = apply_action(ActionStatus.READY, ActionTrigger.POLICY_CONFIRM)
        moved = await ActionRepo(session).cas_status(
            action.id, expected=ActionStatus.READY, new=dest
        )
        if moved is None:
            return GateVerdict(allowed=False, reason="action left READY concurrently")

        await self._audit.append(
            session,
            actor="policy",
            event_type=AuditEvent.POLICY_CONFIRM,
            task_id=task.id,
            action_id=action.id,
            capability_level=action.capability_level,
            payload={"approval_id": approval.id, "tool": action.tool, **decision.to_payload()},
        )
        await self._audit.append(
            session,
            actor="policy",
            event_type=AuditEvent.APPROVAL_REQUESTED,
            task_id=task.id,
            action_id=action.id,
            capability_level=action.capability_level,
            payload={
                "approval_id": approval.id,
                "summary": approval.summary,
                "presented": approval.presented,
                "expires_at": approval.expires_at.isoformat(),
            },
        )
        return GateVerdict(
            allowed=False,
            reason=f"awaiting approval {approval.id}",
            decision=decision,
            approval_id=approval.id,
        )

    async def _refuse_ceiling(
        self, session: AsyncSession, action: Action, task: Task
    ) -> GateVerdict:
        message = (
            f"action requires {action.capability_level.value} but the task ceiling is "
            f"{task.capability_ceiling.value}"
        )
        await self._fail_ready_action(session, action, ErrorCode.PERMISSION_DENIED, message)
        await self._audit.append(
            session,
            actor="policy",
            event_type=AuditEvent.CEILING_EXCEEDED,
            task_id=task.id,
            action_id=action.id,
            capability_level=action.capability_level,
            payload={
                "tool": action.tool,
                "required": action.capability_level.value,
                "ceiling": task.capability_ceiling.value,
            },
        )
        return GateVerdict(allowed=False, reason=message)

    async def _refuse_policy(
        self, session: AsyncSession, action: Action, task: Task, decision: PolicyDecision
    ) -> GateVerdict:
        message = f"policy rule {decision.rule_id} denied this action: {decision.reason}"
        await self._fail_ready_action(session, action, ErrorCode.PERMISSION_DENIED, message)
        await self._audit.append(
            session,
            actor="policy",
            event_type=AuditEvent.POLICY_DENIED,
            task_id=task.id,
            action_id=action.id,
            capability_level=action.capability_level,
            payload={"tool": action.tool, **decision.to_payload()},
        )
        log.warning(
            "astra.security.policy_denied",
            action_id=action.id,
            tool=action.tool,
            rule_id=decision.rule_id,
        )
        return GateVerdict(allowed=False, reason=message, decision=decision)

    async def _fail_ready_action(
        self, session: AsyncSession, action: Action, code: ErrorCode, message: str
    ) -> None:
        repo = ActionRepo(session)
        dest = apply_action(ActionStatus.READY, ActionTrigger.POLICY_DENY)
        await repo.cas_status(
            action.id,
            expected=ActionStatus.READY,
            new=dest,
            finished_at=self._clock.now(),
            lease_owner=None,
            lease_until=None,
            error={"code": code.value, "message": message, "retryable": False, "observation": None},
        )
        await repo.add_dead_letter(
            action_id=action.id, reason=code.value, context={"message": message}
        )

    async def _resume_task(self, session: AsyncSession, task_id: str) -> None:
        task = await TaskRepo(session).get(task_id)
        if task is None or task.status is not TaskStatus.WAITING_FOR_USER:
            return
        dest = apply_task(TaskStatus.WAITING_FOR_USER, TaskTrigger.APPROVAL_GRANTED)
        await TaskRepo(session).cas_status(task_id, expected=TaskStatus.WAITING_FOR_USER, new=dest)

    async def _fail_task_if_blocked(
        self, session: AsyncSession, task_id: str, message: str
    ) -> None:
        """A task waiting only on a refused approval cannot make progress again."""
        repo = TaskRepo(session)
        task = await repo.get(task_id)
        if task is None or task.status is not TaskStatus.WAITING_FOR_USER:
            return
        actions = await ActionRepo(session).list_by_task(task_id)
        if any(not a.status.is_terminal for a in actions):
            return
        dest = apply_task(TaskStatus.WAITING_FOR_USER, TaskTrigger.APPROVAL_REJECTED)
        await repo.cas_status(
            task_id,
            expected=TaskStatus.WAITING_FOR_USER,
            new=dest,
            finished_at=self._clock.now(),
            error={"code": ErrorCode.PERMISSION_DENIED.value, "message": message},
        )


def _workflow_of(task: Task) -> str | None:
    workflow = (task.context_hints or {}).get("workflow")
    return workflow if isinstance(workflow, str) else None


def _summarize(action: Action, step: Step | None) -> str:
    if step is not None:
        return f"{step.title}: {step.intent}"
    return f"Run {action.tool}"


def _presented(
    action: Action, step: Step | None, decision: PolicyDecision, task: Task
) -> dict[str, Any]:
    """The four questions an approval must answer: what, to what exactly, how bad
    if wrong, and can it be undone."""
    parameters = dict(action.parameters)
    if decision.show:
        # A rule may narrow the display to the fields that matter (e.g. an email's
        # recipients and subject). Anything named but absent is shown as missing
        # rather than omitted, so a blank field cannot pass for a safe one.
        parameters = {field: parameters.get(field, "<absent>") for field in decision.show}
    return {
        "task_id": task.id,
        "instruction": task.instruction,
        "intent": step.intent if step is not None else None,
        "tool": action.tool,
        "tool_version": action.tool_version,
        "parameters": parameters,
        "capability_level": action.capability_level.value,
        "policy": decision.to_payload(),
        "verification_plan": list(action.postconditions or []),
    }


def _blast_radius(action: Action) -> dict[str, Any]:
    return {
        "reversible": action.reversible,
        "externally_visible": action.capability_level >= Capability.L3,
        "affects": _affected_count(action),
        "attempt_count": action.attempt_count,
    }


def _affected_count(action: Action) -> int:
    for value in (action.parameters or {}).values():
        if isinstance(value, list):
            return len(value)
    return 1
