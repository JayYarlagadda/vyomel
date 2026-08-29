"""Append-only, hash-chained audit trail (FR-307, threat T9).

``hash = sha256(prev_hash || canonical_json(row_without_hash))``. Reconstructing
the chain from the stored rows must reproduce every hash, so tampering with any
field of any row invalidates that row and every row after it.

Two details that decide whether this works in practice rather than on paper:

**Serialization.** Two processes appending concurrently would both read the same
``prev_hash`` and fork the chain. A transaction-scoped advisory lock makes
appends totally ordered. It is held only for the append, and the audit write
shares the caller's transaction, so a rolled-back action leaves no audit claim
that it happened.

**Timestamp normalization.** ``occurred_at`` is covered by the hash, so its
in-memory and round-tripped forms have to be byte-identical. Both sides go
through :func:`_stamp`, which pins UTC and microsecond precision — Postgres
``timestamptz`` stores exactly that much and no more.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from astra.core.clock import Clock, SystemClock
from astra.core.ids import canonical_json, content_hash
from astra.core.logging import get_logger, redact
from astra.core.types import Capability
from astra.store.models import AUDIT_ID_SEQ, AuditLog

log = get_logger(__name__)

# Arbitrary but fixed: a distinct advisory-lock namespace for chain appends.
_CHAIN_LOCK_KEY = 0x4153545241_01


class AuditEvent:
    """Event type constants. Strings, but not string literals scattered in code."""

    TASK_CREATED = "task.created"
    PLAN_INSTALLED = "plan.installed"
    POLICY_ALLOWED = "policy.allowed"
    POLICY_CONFIRM = "policy.confirm_required"
    POLICY_DENIED = "policy.denied"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_MODIFIED = "approval.modified"
    APPROVAL_REJECTED = "approval.rejected"
    APPROVAL_EXPIRED = "approval.expired"
    APPROVAL_CONSUMED = "approval.consumed"
    ACTION_DISPATCHED = "action.dispatched"
    ACTION_FINISHED = "action.finished"
    TOOL_INVOKED = "tool.invoked"
    ACTION_COMPENSATED = "action.compensated"
    TASK_CANCELLED = "task.cancelled"
    VERIFICATION_COMPLETED = "verification.completed"
    CEILING_EXCEEDED = "policy.ceiling_exceeded"


@dataclass(frozen=True, slots=True)
class ChainReport:
    ok: bool
    rows: int
    first_divergence_id: int | None = None
    detail: str | None = None


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _digest(
    *,
    row_id: int,
    occurred_at: datetime,
    actor: str,
    event_type: str,
    task_id: str | None,
    action_id: str | None,
    capability_level: Capability | None,
    payload: dict[str, Any],
    prev_hash: str | None,
) -> str:
    """The one definition of a row's hash. Used by both append and verify, so the
    two cannot drift into disagreeing about what was signed."""
    body = canonical_json(
        {
            "id": row_id,
            "occurred_at": _stamp(occurred_at),
            "actor": actor,
            "event_type": event_type,
            "task_id": task_id,
            "action_id": action_id,
            "capability_level": capability_level.value if capability_level else None,
            "payload": payload,
        }
    )
    return content_hash((prev_hash or "", body))


class AuditTrail:
    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()

    async def append(
        self,
        session: AsyncSession,
        *,
        actor: str,
        event_type: str,
        task_id: str | None = None,
        action_id: str | None = None,
        capability_level: Capability | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AuditLog:
        """Add one record to the chain, inside the caller's transaction."""
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CHAIN_LOCK_KEY})
        prev_hash = (
            await session.scalar(select(AuditLog.hash).order_by(AuditLog.id.desc()).limit(1))
        ) or None
        row_id = await session.scalar(select(AUDIT_ID_SEQ.next_value()))
        assert row_id is not None
        occurred_at = self._clock.now()
        # Redacted before write, not before read: an audit record containing a
        # secret is a leak that persists forever (NFR-09).
        safe_payload: dict[str, Any] = dict(redact(payload or {}))

        record = AuditLog(
            id=row_id,
            occurred_at=occurred_at,
            actor=actor,
            event_type=event_type,
            task_id=task_id,
            action_id=action_id,
            capability_level=capability_level,
            payload=safe_payload,
            prev_hash=prev_hash,
            hash=_digest(
                row_id=row_id,
                occurred_at=occurred_at,
                actor=actor,
                event_type=event_type,
                task_id=task_id,
                action_id=action_id,
                capability_level=capability_level,
                payload=safe_payload,
                prev_hash=prev_hash,
            ),
        )
        session.add(record)
        await session.flush()
        return record

    async def records(
        self,
        session: AsyncSession,
        *,
        task_id: str | None = None,
        action_id: str | None = None,
        event_type: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditLog]:
        """Read the trail. Ordered newest first: callers are almost always
        asking what just happened."""
        stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
        if task_id is not None:
            stmt = stmt.where(AuditLog.task_id == task_id)
        if action_id is not None:
            stmt = stmt.where(AuditLog.action_id == action_id)
        if event_type is not None:
            stmt = stmt.where(AuditLog.event_type == event_type)
        if since is not None:
            stmt = stmt.where(AuditLog.occurred_at >= since)
        return list((await session.execute(stmt)).scalars())

    async def verify(self, session: AsyncSession, *, start_id: int | None = None) -> ChainReport:
        """Walk the chain and report the first row that does not reproduce.

        ``start_id`` verifies a suffix, seeding the expected ``prev_hash`` from
        the row immediately before it. ``astra audit verify`` walks everything;
        a caller interested in one task's segment does not have to.
        """
        stmt = select(AuditLog).order_by(AuditLog.id)
        prev_hash: str | None = None
        if start_id is not None:
            stmt = stmt.where(AuditLog.id >= start_id)
            prev_hash = await session.scalar(
                select(AuditLog.hash)
                .where(AuditLog.id < start_id)
                .order_by(AuditLog.id.desc())
                .limit(1)
            )
        rows = list((await session.execute(stmt)).scalars())
        for row in rows:
            if row.prev_hash != prev_hash:
                return ChainReport(
                    ok=False,
                    rows=len(rows),
                    first_divergence_id=row.id,
                    detail="prev_hash does not match the preceding row",
                )
            expected = _digest(
                row_id=row.id,
                occurred_at=row.occurred_at,
                actor=row.actor,
                event_type=row.event_type,
                task_id=row.task_id,
                action_id=row.action_id,
                capability_level=row.capability_level,
                payload=row.payload,
                prev_hash=row.prev_hash,
            )
            if expected != row.hash:
                return ChainReport(
                    ok=False,
                    rows=len(rows),
                    first_divergence_id=row.id,
                    detail="row contents do not match the recorded hash",
                )
            prev_hash = row.hash
        return ChainReport(ok=True, rows=len(rows))
