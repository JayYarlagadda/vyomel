"""The gate between READY and DISPATCHED (FR-303, FR-304).

These run the real scheduler against real Postgres and Redis. A gate that works
in a unit test but not inside a dispatch tick would be worse than no gate at
all, because it would be believed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from tests.fakes import Notify
from tests.runtime.helpers import install_plan
from vyomel.core.config import Settings
from vyomel.core.errors import ErrorCode
from vyomel.core.types import ActionStatus, ApprovalStatus, Capability, TaskStatus
from vyomel.orchestrator.approvals import ApprovalWorkflow
from vyomel.orchestrator.plans import ActionSpec, HandwrittenPlan, PlanError, StepSpec
from vyomel.orchestrator.runtime import get_registry
from vyomel.runtime.scheduler import Scheduler
from vyomel.runtime.worker import Worker
from vyomel.security.audit import AuditEvent
from vyomel.store.db import session_scope
from vyomel.store.models import Action, Approval, AuditLog, Task


def notify_plan(recipient: str = "someone@example.com") -> HandwrittenPlan:
    return HandwrittenPlan(
        steps=[
            StepSpec(
                alias="tell",
                title="Notify the recipient",
                intent="Tell someone outside this machine that the job is done",
                actions=[
                    ActionSpec(
                        alias="n",
                        tool="test.notify",
                        parameters={"recipient": recipient, "body": "done"},
                    )
                ],
            )
        ]
    )


def read_plan(path: Path) -> HandwrittenPlan:
    return HandwrittenPlan(
        steps=[
            StepSpec(
                alias="r",
                title="Read",
                intent="read a file",
                actions=[
                    ActionSpec(alias="rd", tool="fs.read_file", parameters={"path": str(path)})
                ],
            )
        ]
    )


async def only_action(task_id: str) -> Action:
    async with session_scope() as session:
        return (await session.execute(select(Action).where(Action.task_id == task_id))).scalar_one()


async def only_approval(task_id: str) -> Approval:
    async with session_scope() as session:
        return (
            await session.execute(select(Approval).where(Approval.task_id == task_id))
        ).scalar_one()


async def events_for(task_id: str) -> list[str]:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(AuditLog.event_type).where(AuditLog.task_id == task_id).order_by(AuditLog.id)
            )
        ).scalars()
        return list(rows)


@pytest.mark.integration
@pytest.mark.req("FR-303")
async def test_an_l3_action_blocks_for_approval_instead_of_running(
    runtime_db: Settings, scheduler: Scheduler, worker: Worker
) -> None:
    Notify.delivered.clear()
    task = await install_plan(
        runtime_db, notify_plan(), instruction="tell someone", ceiling=Capability.L3
    )

    published = await scheduler.tick()

    assert published == 0, "an action awaiting approval must not reach the queue"
    action = await only_action(task.id)
    assert action.status is ActionStatus.WAITING_FOR_USER
    assert Notify.delivered == []

    # A worker given the chance finds nothing to claim.
    assert await worker.run_once(block_ms=50) is False

    approval = await only_approval(task.id)
    assert approval.status is ApprovalStatus.PENDING
    assert approval.capability_level is Capability.L3
    assert approval.expires_at > approval.created_at

    assert AuditEvent.POLICY_CONFIRM in await events_for(task.id)
    assert AuditEvent.APPROVAL_REQUESTED in await events_for(task.id)


@pytest.mark.integration
@pytest.mark.req("FR-304")
async def test_the_approval_shows_what_will_happen_to_what_and_how_bad(
    runtime_db: Settings, scheduler: Scheduler
) -> None:
    task = await install_plan(runtime_db, notify_plan("dean@example.edu"), ceiling=Capability.L3)
    await scheduler.tick()
    approval = await only_approval(task.id)

    presented = approval.presented
    assert presented["tool"] == "test.notify"
    assert presented["parameters"]["recipient"] == "dean@example.edu"
    assert presented["capability_level"] == "L3"
    assert presented["intent"]
    assert presented["policy"]["rule_id"]
    assert presented["policy"]["policy_hash"]

    assert approval.blast_radius["externally_visible"] is True
    assert approval.blast_radius["reversible"] is False
    assert approval.summary


@pytest.mark.integration
@pytest.mark.req("FR-303")
async def test_granting_an_approval_lets_the_action_run_exactly_once(
    runtime_db: Settings, scheduler: Scheduler, worker: Worker
) -> None:
    Notify.delivered.clear()
    task = await install_plan(runtime_db, notify_plan("ops@example.com"), ceiling=Capability.L3)
    await scheduler.tick()
    approval = await only_approval(task.id)

    async with session_scope() as session:
        await ApprovalWorkflow(session, runtime_db, get_registry()).approve(
            approval.id, decided_by="tester"
        )

    assert await scheduler.tick() == 1
    assert await worker.run_once(block_ms=200)

    action = await only_action(task.id)
    # value_equals now exists, so a notify whose result matches the presented
    # recipient is SUCCEEDED rather than UNVERIFIED.
    assert action.status is ActionStatus.SUCCEEDED
    assert Notify.delivered == ["ops@example.com"]

    async with session_scope() as session:
        stored = await session.get(Approval, approval.id)
        assert stored is not None
        assert stored.status is ApprovalStatus.APPROVED
        assert stored.consumed_at is not None
        assert stored.decided_by == "tester"

    events = await events_for(task.id)
    assert AuditEvent.APPROVAL_GRANTED in events
    assert AuditEvent.APPROVAL_CONSUMED in events


@pytest.mark.integration
@pytest.mark.req("FR-303")
async def test_rejecting_an_approval_fails_the_action_and_the_task(
    runtime_db: Settings, scheduler: Scheduler, worker: Worker
) -> None:
    Notify.delivered.clear()
    task = await install_plan(runtime_db, notify_plan(), ceiling=Capability.L3)
    await scheduler.tick()
    approval = await only_approval(task.id)

    async with session_scope() as session:
        await ApprovalWorkflow(session, runtime_db, get_registry()).reject(
            approval.id, decided_by="tester", reason="not sending that"
        )

    await scheduler.tick()

    action = await only_action(task.id)
    assert action.status is ActionStatus.FAILED
    assert action.error is not None
    assert action.error["code"] == ErrorCode.PERMISSION_DENIED.value
    assert action.error["retryable"] is False
    assert Notify.delivered == []

    async with session_scope() as session:
        stored_task = await session.get(Task, task.id)
        assert stored_task is not None
        assert stored_task.status is TaskStatus.FAILED
    assert AuditEvent.APPROVAL_REJECTED in await events_for(task.id)


@pytest.mark.integration
@pytest.mark.req("FR-303")
async def test_a_spent_approval_cannot_authorize_a_second_dispatch(
    runtime_db: Settings, scheduler: Scheduler, worker: Worker
) -> None:
    """Single-use. A replay after a crash must ask again, not re-authorize."""
    task = await install_plan(runtime_db, notify_plan(), ceiling=Capability.L3)
    await scheduler.tick()
    approval = await only_approval(task.id)
    async with session_scope() as session:
        await ApprovalWorkflow(session, runtime_db, get_registry()).approve(
            approval.id, decided_by="tester"
        )
    await scheduler.tick()  # consumes the approval and dispatches

    # Simulate the action being returned to READY by a lease expiry.
    async with session_scope() as session:
        action = await session.get(Action, (await only_action(task.id)).id)
        assert action is not None
        action.status = ActionStatus.READY
        action.lease_owner = None
        action.lease_until = None

    await scheduler.tick()

    action = await only_action(task.id)
    assert action.status is ActionStatus.WAITING_FOR_USER
    async with session_scope() as session:
        approvals = list(
            (await session.execute(select(Approval).where(Approval.task_id == task.id))).scalars()
        )
    assert len(approvals) == 2, "a fresh approval is requested rather than reusing the spent one"
    assert {a.status for a in approvals} == {ApprovalStatus.APPROVED, ApprovalStatus.PENDING}


@pytest.mark.integration
@pytest.mark.req("FR-303")
async def test_editing_parameters_after_approval_voids_it(
    runtime_db: Settings, scheduler: Scheduler
) -> None:
    """Approve, then tamper with the row. The approval no longer covers it."""
    task = await install_plan(
        runtime_db, notify_plan("intended@example.com"), ceiling=Capability.L3
    )
    await scheduler.tick()
    approval = await only_approval(task.id)
    async with session_scope() as session:
        await ApprovalWorkflow(session, runtime_db, get_registry()).approve(
            approval.id, decided_by="tester"
        )

    async with session_scope() as session:
        action = await session.get(Action, (await only_action(task.id)).id)
        assert action is not None
        assert action.status is ActionStatus.READY
        action.parameters = {"recipient": "attacker@example.com", "body": "done"}

    published = await scheduler.tick()

    assert published == 0
    action = await only_action(task.id)
    assert action.status is ActionStatus.WAITING_FOR_USER
    async with session_scope() as session:
        stored = await session.get(Approval, approval.id)
        assert stored is not None
        assert stored.consumed_at is None, "the tampered invocation must not consume the approval"


@pytest.mark.integration
@pytest.mark.req("FR-302")
async def test_a_denied_path_fails_the_action_without_asking_anyone(
    runtime_db: Settings, scheduler: Scheduler, tmp_path: Path
) -> None:
    """A credential path is classified L4 *and* denied by rule. Deny wins, and no
    approval is created — there is nothing for a human to usefully consent to."""
    secret = tmp_path / ".env"
    secret.write_text("VYOMEL_API_TOKEN=nope", encoding="utf-8")
    task = await install_plan(
        runtime_db, read_plan(secret), instruction="read the env file", ceiling=Capability.L4
    )

    published = await scheduler.tick()

    assert published == 0
    action = await only_action(task.id)
    assert action.status is ActionStatus.FAILED
    assert action.capability_level is Capability.L4  # escalated by the sensitive path
    async with session_scope() as session:
        approvals = list(
            (await session.execute(select(Approval).where(Approval.task_id == task.id))).scalars()
        )
    assert approvals == []
    assert AuditEvent.POLICY_DENIED in await events_for(task.id)


@pytest.mark.integration
@pytest.mark.req("FR-301")
async def test_a_plan_above_the_task_ceiling_is_rejected_at_install(
    runtime_db: Settings, tmp_path: Path
) -> None:
    """The ceiling is the user's up-front consent boundary (docs/06 section 5)."""
    with pytest.raises(PlanError, match="ceiling"):
        await install_plan(runtime_db, notify_plan(), ceiling=Capability.L2)
