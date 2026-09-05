"""Approval records (FR-303, FR-304, FR-305).

This module owns the ``approvals`` table and the rules that make an approval
*mean* something:

- An approval is bound to ``(action_id, parameter_hash)``. It authorizes one
  invocation with one set of parameters, not a tool.
- It is single-use: dispatch consumes it. A crash-replay of the same action
  finds it spent and asks again rather than silently re-authorizing.
- Expiry fails closed. An unanswered approval never becomes an approval.

It deliberately does **not** move actions between states. The action state
machine lives in ``vyomel.runtime.state``, and the layering rules forbid the
security layer from reaching into the runtime; ``vyomel.runtime.gate`` is the
component that holds both halves. The practical benefit is that this module is
testable without a scheduler.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vyomel.core.clock import Clock, SystemClock
from vyomel.core.errors import ConflictError, NotFoundError
from vyomel.core.ids import content_hash
from vyomel.core.logging import get_logger
from vyomel.core.types import ApprovalStatus, Capability
from vyomel.store.models import Approval

log = get_logger(__name__)


def parameter_hash(parameters: dict[str, Any]) -> str:
    """Identity of an invocation's parameters, for approval binding."""
    return content_hash(parameters)


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """Everything the user must see before consenting (FR-304).

    ``presented`` is stored verbatim so the audit record reflects what was
    actually shown, not what the system meant to show.
    """

    action_id: str
    task_id: str
    capability_level: Capability
    summary: str
    presented: dict[str, Any]
    blast_radius: dict[str, Any]
    parameter_hash: str
    policy_rule_id: str | None = None
    policy_hash: str | None = None


class ApprovalService:
    def __init__(self, session: AsyncSession, *, ttl_s: int, clock: Clock | None = None) -> None:
        self._session = session
        self._ttl_s = ttl_s
        self._clock = clock or SystemClock()

    async def get(self, approval_id: str) -> Approval:
        approval = await self._session.get(Approval, approval_id)
        if approval is None:
            raise NotFoundError(f"approval {approval_id} not found")
        return approval

    async def create(self, request: ApprovalRequest) -> Approval:
        approval = Approval(
            action_id=request.action_id,
            task_id=request.task_id,
            capability_level=request.capability_level,
            summary=request.summary,
            presented=request.presented,
            blast_radius=request.blast_radius,
            status=ApprovalStatus.PENDING,
            parameter_hash=request.parameter_hash,
            policy_rule_id=request.policy_rule_id,
            policy_hash=request.policy_hash,
            expires_at=self._clock.now() + timedelta(seconds=self._ttl_s),
        )
        self._session.add(approval)
        await self._session.flush()
        log.info(
            "vyomel.security.approval_requested",
            approval_id=approval.id,
            action_id=approval.action_id,
            level=approval.capability_level.value,
            expires_at=approval.expires_at.isoformat(),
        )
        return approval

    async def pending_for(self, action_id: str) -> Approval | None:
        result = await self._session.execute(
            select(Approval).where(
                Approval.action_id == action_id, Approval.status == ApprovalStatus.PENDING
            )
        )
        return result.scalar_one_or_none()

    async def usable_for(
        self, action_id: str, params_hash: str, level: Capability
    ) -> Approval | None:
        """A decided, unconsumed, unexpired approval covering this exact invocation.

        Both conditions are anti-tampering controls:

        - ``parameter_hash`` — approve, then edit the action row, and the
          approval no longer applies.
        - ``capability_level`` — an approval granted at L2 cannot cover an action
          that has since been re-classified L3. Consent is to a blast radius,
          not to an action id.
        """
        candidates = list(
            (
                await self._session.execute(
                    select(Approval)
                    .where(
                        Approval.action_id == action_id,
                        Approval.status.in_((ApprovalStatus.APPROVED, ApprovalStatus.MODIFIED)),
                        Approval.consumed_at.is_(None),
                    )
                    .order_by(Approval.id.desc())
                )
            ).scalars()
        )
        now = self._clock.now()
        for approval in candidates:
            if approval.parameter_hash != params_hash:
                continue
            if approval.capability_level < level:
                continue
            if approval.expires_at <= now:
                continue
            return approval
        return None

    async def consume(self, approval: Approval) -> Approval:
        if approval.consumed_at is not None:
            raise ConflictError(f"approval {approval.id} has already been used")
        approval.consumed_at = self._clock.now()
        await self._session.flush()
        return approval

    async def approve(
        self,
        approval: Approval,
        *,
        decided_by: str,
        modified_parameters: dict[str, Any] | None = None,
        new_parameter_hash: str | None = None,
    ) -> Approval:
        """Record a positive decision.

        A modification rebinds the approval to the *edited* parameters. The
        caller is responsible for having re-validated and re-classified them
        first: this method cannot tell an innocuous edit from an escalation, and
        anything that can silently widen an approval does not belong here.
        """
        self._assert_pending(approval)
        approval.status = (
            ApprovalStatus.MODIFIED if modified_parameters is not None else ApprovalStatus.APPROVED
        )
        approval.modified_parameters = modified_parameters
        if new_parameter_hash is not None:
            approval.parameter_hash = new_parameter_hash
        approval.decided_by = decided_by
        approval.decided_at = self._clock.now()
        await self._session.flush()
        log.info(
            "vyomel.security.approval_decided",
            approval_id=approval.id,
            status=approval.status.value,
            decided_by=decided_by,
        )
        return approval

    async def reject(self, approval: Approval, *, decided_by: str) -> Approval:
        self._assert_pending(approval)
        approval.status = ApprovalStatus.REJECTED
        approval.decided_by = decided_by
        approval.decided_at = self._clock.now()
        await self._session.flush()
        log.info("vyomel.security.approval_rejected", approval_id=approval.id)
        return approval

    async def due_for_expiry(self, now: datetime | None = None) -> list[Approval]:
        moment = now or self._clock.now()
        return list(
            (
                await self._session.execute(
                    select(Approval).where(
                        Approval.status == ApprovalStatus.PENDING,
                        Approval.expires_at <= moment,
                    )
                )
            ).scalars()
        )

    async def mark_expired(self, approval: Approval) -> Approval:
        approval.status = ApprovalStatus.EXPIRED
        approval.decided_at = self._clock.now()
        approval.decided_by = "system:expiry"
        await self._session.flush()
        log.info("vyomel.security.approval_expired", approval_id=approval.id)
        return approval

    async def list(
        self,
        *,
        status: ApprovalStatus | None = None,
        task_id: str | None = None,
        limit: int = 50,
    ) -> list[Approval]:
        stmt = select(Approval).order_by(Approval.created_at.desc()).limit(limit)
        if status is not None:
            stmt = stmt.where(Approval.status == status)
        if task_id is not None:
            stmt = stmt.where(Approval.task_id == task_id)
        return list((await self._session.execute(stmt)).scalars())

    def _assert_pending(self, approval: Approval) -> None:
        if approval.status is not ApprovalStatus.PENDING:
            raise ConflictError(
                f"approval {approval.id} is already {approval.status.value}",
                detail={"status": approval.status.value},
            )
        if approval.expires_at <= self._clock.now():
            # Expiry is checked on the decision path as well as by the sweeper:
            # a decision that arrives after the deadline must not win a race
            # against the sweeper that has not run yet (FR-305).
            raise ConflictError(
                f"approval {approval.id} expired at {approval.expires_at.isoformat()}",
                detail={"expired_at": approval.expires_at.isoformat()},
            )
