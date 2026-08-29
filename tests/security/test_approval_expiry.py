"""Unanswered approvals fail closed (FR-305).

Silence is not consent. An approval that nobody answers must leave the action
FAILED, never dispatched — and the decision must be refused even if it arrives
one millisecond after the deadline, because the alternative is a race between
the user and the sweeper deciding whether an email gets sent.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from astra.core.clock import FrozenClock
from astra.core.config import Settings
from astra.core.errors import ConflictError, ErrorCode
from astra.core.types import ActionStatus, ApprovalStatus, Capability, TaskStatus
from astra.orchestrator.approvals import ApprovalWorkflow
from astra.orchestrator.runtime import get_registry, make_scheduler
from astra.runtime.gate import PolicyGate
from astra.runtime.queue import ActionQueue
from astra.security.audit import AuditEvent
from astra.security.policy import store_for
from astra.store.db import session_scope
from astra.store.models import Action, Approval, AuditLog, Task
from tests.fakes import Notify
from tests.runtime.helpers import install_plan
from tests.security.test_approval_gate import notify_plan, only_action, only_approval


@pytest.mark.integration
@pytest.mark.req("FR-305")
async def test_an_unanswered_approval_expires_and_fails_the_action_closed(
    runtime_db: Settings, queue: ActionQueue
) -> None:
    Notify.delivered.clear()
    clock = FrozenClock()
    gate = PolicyGate(store_for(runtime_db), approval_ttl_s=runtime_db.approval_ttl_s, clock=clock)
    # The scheduler shares the frozen clock's gate so that advancing time here
    # advances it for the sweeper too.
    scheduler = make_scheduler(runtime_db, queue, clock=clock, gate=gate)

    task = await install_plan(runtime_db, notify_plan(), ceiling=Capability.L3)
    await scheduler.tick()

    approval = await only_approval(task.id)
    assert approval.status is ApprovalStatus.PENDING
    assert (await only_action(task.id)).status is ActionStatus.WAITING_FOR_USER

    # One second before the deadline: still pending, still blocked.
    clock.advance(runtime_db.approval_ttl_s - 1)
    await scheduler.tick()
    async with session_scope() as session:
        still = await session.get(Approval, approval.id)
        assert still is not None
        assert still.status is ApprovalStatus.PENDING

    clock.advance(2)
    published = await scheduler.tick()

    assert published == 0
    async with session_scope() as session:
        expired = await session.get(Approval, approval.id)
        assert expired is not None
        assert expired.status is ApprovalStatus.EXPIRED
        assert expired.decided_by == "system:expiry"

    action = await only_action(task.id)
    assert action.status is ActionStatus.FAILED
    assert action.error is not None
    assert action.error["code"] == ErrorCode.PERMISSION_DENIED.value
    assert "expired" in action.error["message"]
    assert Notify.delivered == []

    # The task must not be left parked in WAITING_FOR_USER.
    await scheduler.tick()
    async with session_scope() as session:
        stored = await session.get(Task, task.id)
        assert stored is not None
        assert stored.status is TaskStatus.FAILED
        events = list(
            (
                await session.execute(
                    select(AuditLog.event_type).where(AuditLog.task_id == task.id)
                )
            ).scalars()
        )
    assert AuditEvent.APPROVAL_EXPIRED in events


@pytest.mark.integration
@pytest.mark.req("FR-305")
async def test_a_decision_arriving_after_the_deadline_is_refused(
    runtime_db: Settings, queue: ActionQueue
) -> None:
    """The sweeper may not have run yet. The decision path checks the clock too."""
    Notify.delivered.clear()
    clock = FrozenClock()
    gate = PolicyGate(store_for(runtime_db), approval_ttl_s=runtime_db.approval_ttl_s, clock=clock)
    scheduler = make_scheduler(runtime_db, queue, clock=clock, gate=gate)

    task = await install_plan(runtime_db, notify_plan(), ceiling=Capability.L3)
    await scheduler.tick()
    approval = await only_approval(task.id)

    clock.advance(runtime_db.approval_ttl_s + 1)

    async with session_scope() as session:
        workflow = ApprovalWorkflow(session, runtime_db, get_registry(), gate=gate, clock=clock)
        with pytest.raises(ConflictError, match="expired"):
            await workflow.approve(approval.id, decided_by="late-tester")

    async with session_scope() as session:
        untouched = await session.get(Approval, approval.id)
        assert untouched is not None
        assert untouched.status is ApprovalStatus.PENDING  # rolled back, not approved
    assert (await only_action(task.id)).status is ActionStatus.WAITING_FOR_USER
    assert Notify.delivered == []


@pytest.mark.integration
@pytest.mark.req("FR-305")
async def test_expiry_does_not_touch_a_decided_approval(
    runtime_db: Settings, queue: ActionQueue
) -> None:
    clock = FrozenClock()
    gate = PolicyGate(store_for(runtime_db), approval_ttl_s=runtime_db.approval_ttl_s, clock=clock)
    scheduler = make_scheduler(runtime_db, queue, clock=clock, gate=gate)

    task = await install_plan(runtime_db, notify_plan(), ceiling=Capability.L3)
    await scheduler.tick()
    approval = await only_approval(task.id)

    async with session_scope() as session:
        await ApprovalWorkflow(session, runtime_db, get_registry(), gate=gate, clock=clock).approve(
            approval.id, decided_by="tester"
        )

    clock.advance(runtime_db.approval_ttl_s + 60)
    async with session_scope() as session:
        expired = await gate.expire_overdue(session)
    assert approval.id not in expired

    async with session_scope() as session:
        stored = await session.get(Approval, approval.id)
        assert stored is not None
        assert stored.status is ApprovalStatus.APPROVED


@pytest.mark.integration
@pytest.mark.req("FR-305")
async def test_an_expired_approval_cannot_be_consumed_even_if_never_swept(
    runtime_db: Settings, queue: ActionQueue
) -> None:
    """Belt and braces: consumption re-checks expiry independently of the sweep."""
    clock = FrozenClock()
    gate = PolicyGate(store_for(runtime_db), approval_ttl_s=runtime_db.approval_ttl_s, clock=clock)
    scheduler = make_scheduler(runtime_db, queue, clock=clock, gate=gate)

    task = await install_plan(runtime_db, notify_plan(), ceiling=Capability.L3)
    await scheduler.tick()
    approval = await only_approval(task.id)
    async with session_scope() as session:
        await ApprovalWorkflow(session, runtime_db, get_registry(), gate=gate, clock=clock).approve(
            approval.id, decided_by="tester"
        )

    # The action is READY with a granted approval, but nothing dispatches it
    # before the approval's own expiry passes.
    clock.advance(runtime_db.approval_ttl_s + 1)
    async with session_scope() as session:
        action = (
            await session.execute(select(Action).where(Action.task_id == task.id))
        ).scalar_one()
        service = gate.service(session)
        usable = await service.usable_for(
            action.id, approval.parameter_hash, action.capability_level
        )
    assert usable is None
