"""The audit trail is append-only and tamper-evident (FR-307, threat T9).

Two independent controls, tested independently:

- A ``BEFORE UPDATE OR DELETE`` trigger *blocks* modification.
- The hash chain *detects* modification, for the case where the attacker has
  enough privilege to drop the trigger.

The tamper tests disable the trigger to make the mutation possible, then restore
both the row and the trigger in a ``finally``. That is deliberate: the chain must
be provably able to catch a change, and a test that cannot make the change proves
nothing. The restore is not politeness — the log is append-only and shared by the
whole session, so a test that left it broken would fail every later one.

Verification is scoped with ``start_id`` for the same reason: each test asserts
about the segment it wrote, not about every row any other test happened to
append.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from astra.core.clock import FrozenClock
from astra.core.config import Settings
from astra.core.types import Capability
from astra.security.audit import AuditEvent, AuditTrail
from astra.store.db import session_scope
from astra.store.models import AuditLog


async def _append(trail: AuditTrail, event_type: str, **kwargs: object) -> int:
    async with session_scope() as session:
        record = await trail.append(
            session,
            actor="policy",
            event_type=event_type,
            **kwargs,  # type: ignore[arg-type]
        )
        return record.id


@pytest.mark.integration
@pytest.mark.req("FR-307")
async def test_appends_form_a_verifiable_chain(runtime_db: Settings) -> None:
    trail = AuditTrail(FrozenClock())
    ids = [
        await _append(trail, AuditEvent.POLICY_ALLOWED, task_id="T1"),
        await _append(trail, AuditEvent.ACTION_DISPATCHED, task_id="T1", action_id="A1"),
        await _append(
            trail,
            AuditEvent.APPROVAL_GRANTED,
            task_id="T1",
            action_id="A1",
            capability_level=Capability.L3,
        ),
    ]
    assert ids == sorted(ids)

    async with session_scope() as session:
        report = await trail.verify(session, start_id=ids[0])
        rows = list(
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.id.in_(ids)).order_by(AuditLog.id)
                )
            ).scalars()
        )

    assert report.ok, report.detail
    assert rows[1].prev_hash == rows[0].hash
    assert rows[2].prev_hash == rows[1].hash
    assert len({row.hash for row in rows}) == 3


@pytest.mark.integration
@pytest.mark.req("FR-307")
async def test_the_payload_is_redacted_before_it_is_stored(runtime_db: Settings) -> None:
    trail = AuditTrail(FrozenClock())
    leaked = "sk-" + "abcdefghijklmnopqrstuvwxyz012345"
    row_id = await _append(
        trail,
        AuditEvent.POLICY_ALLOWED,
        task_id="T-redact",
        payload={"api_key": "plain", "prompt": f"use {leaked} to authenticate"},
    )

    async with session_scope() as session:
        stored = await session.get(AuditLog, row_id)
        assert stored is not None
        assert stored.payload["api_key"] != "plain"
        assert leaked not in stored.payload["prompt"]
        report = await trail.verify(session, start_id=row_id)
    assert report.ok, report.detail


@pytest.mark.integration
@pytest.mark.req("FR-307")
async def test_update_and_delete_are_blocked_by_the_database(runtime_db: Settings) -> None:
    trail = AuditTrail(FrozenClock())
    row_id = await _append(trail, AuditEvent.POLICY_DENIED, task_id="T-immutable")

    with pytest.raises(DBAPIError, match="append-only"):
        async with session_scope() as session:
            await session.execute(
                text("UPDATE audit_log SET actor = 'someone-else' WHERE id = :id"), {"id": row_id}
            )

    with pytest.raises(DBAPIError, match="append-only"):
        async with session_scope() as session:
            await session.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": row_id})

    async with session_scope() as session:
        survivor = await session.get(AuditLog, row_id)
        assert survivor is not None
        assert survivor.actor == "policy"


@pytest.mark.integration
@pytest.mark.req("FR-307")
async def test_a_mutated_row_is_detected(runtime_db: Settings) -> None:
    trail = AuditTrail(FrozenClock())
    row_id = await _append(
        trail, AuditEvent.APPROVAL_GRANTED, task_id="T-tamper", payload={"amount": 5}
    )
    await _append(trail, AuditEvent.ACTION_FINISHED, task_id="T-tamper")

    async with _trigger_disabled():
        try:
            async with session_scope() as session:
                await session.execute(
                    text("""UPDATE audit_log SET payload = '{"amount": 50000}' WHERE id = :id"""),
                    {"id": row_id},
                )
            async with session_scope() as session:
                report = await trail.verify(session, start_id=row_id)
            assert not report.ok
            assert report.first_divergence_id == row_id
            assert report.detail == "row contents do not match the recorded hash"
        finally:
            async with session_scope() as session:
                await session.execute(
                    text("""UPDATE audit_log SET payload = '{"amount": 5}' WHERE id = :id"""),
                    {"id": row_id},
                )

    async with session_scope() as session:
        assert (await trail.verify(session, start_id=row_id)).ok


@pytest.mark.integration
@pytest.mark.req("FR-307")
async def test_a_deleted_row_breaks_the_chain(runtime_db: Settings) -> None:
    trail = AuditTrail(FrozenClock())
    first = await _append(trail, AuditEvent.POLICY_ALLOWED, task_id="T-gap")
    middle = await _append(trail, AuditEvent.POLICY_CONFIRM, task_id="T-gap")
    last = await _append(trail, AuditEvent.ACTION_DISPATCHED, task_id="T-gap")

    async with _trigger_disabled():
        async with session_scope() as session:
            row = await session.get(AuditLog, middle)
            assert row is not None
            removed = {
                "id": row.id,
                "occurred_at": row.occurred_at,
                "actor": row.actor,
                "event_type": row.event_type,
                "task_id": row.task_id,
                "action_id": row.action_id,
                "payload": json.dumps(row.payload),
                "prev_hash": row.prev_hash,
                "hash": row.hash,
            }
        try:
            async with session_scope() as session:
                await session.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": middle})
            async with session_scope() as session:
                report = await trail.verify(session, start_id=first)
            assert not report.ok
            assert report.first_divergence_id == last
            assert report.detail == "prev_hash does not match the preceding row"
        finally:
            async with session_scope() as session:
                await session.execute(
                    text(
                        "INSERT INTO audit_log (id, occurred_at, actor, event_type, task_id, "
                        "action_id, payload, prev_hash, hash) VALUES (:id, :occurred_at, :actor, "
                        ":event_type, :task_id, :action_id, :payload, :prev_hash, :hash) "
                        "ON CONFLICT (id) DO NOTHING"
                    ),
                    removed,
                )

    async with session_scope() as session:
        report = await trail.verify(session, start_id=first)
    assert report.ok, report.detail
    assert first < middle < last


def _trigger_disabled() -> _TriggerDisabled:
    return _TriggerDisabled()


class _TriggerDisabled:
    """Context manager that lifts the append-only trigger for a tamper test."""

    async def __aenter__(self) -> None:
        async with session_scope() as session:
            await session.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_immutable"))

    async def __aexit__(self, *_exc: object) -> None:
        async with session_scope() as session:
            await session.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_immutable"))
