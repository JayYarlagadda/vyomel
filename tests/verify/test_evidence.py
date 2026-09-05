"""Every verification outcome is recorded with evidence (FR-405)."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from tests.runtime.helpers import drain, install_plan

from vyomel.core.config import Settings
from vyomel.core.types import ActionStatus, VerifyOutcome
from vyomel.orchestrator.plans import ActionSpec, HandwrittenPlan, StepSpec
from vyomel.runtime.scheduler import Scheduler
from vyomel.runtime.worker import Worker
from vyomel.security.audit import AuditEvent
from vyomel.store.db import session_scope
from vyomel.store.models import Action, AuditLog, Verification


def _report_plan() -> HandwrittenPlan:
    return HandwrittenPlan(
        steps=[
            StepSpec(
                alias="wrap",
                title="Report",
                intent="Say what happened",
                actions=[
                    ActionSpec(
                        alias="rep",
                        tool="task.report",
                        parameters={"summary": "nothing to write", "findings": []},
                    )
                ],
            )
        ]
    )


@pytest.mark.integration
@pytest.mark.req("FR-405")
async def test_verification_is_persisted_and_audited(
    runtime_db: Settings, scheduler: Scheduler, worker: Worker
) -> None:
    task = await install_plan(runtime_db, _report_plan(), instruction="report")
    await drain(scheduler, worker)

    async with session_scope() as session:
        action = (
            await session.execute(select(Action).where(Action.task_id == task.id))
        ).scalar_one()
        rows = list(
            (
                await session.execute(
                    select(Verification).where(Verification.action_id == action.id)
                )
            ).scalars()
        )
        events = list(
            (
                await session.execute(
                    select(AuditLog.event_type)
                    .where(AuditLog.task_id == task.id)
                    .order_by(AuditLog.id)
                )
            ).scalars()
        )
        audited = list(
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.task_id == task.id,
                        AuditLog.event_type == AuditEvent.VERIFICATION_COMPLETED,
                    )
                )
            ).scalars()
        )

    assert action.status is ActionStatus.SUCCEEDED
    assert len(rows) == 1
    assert rows[0].verifier == "l0_result_present"
    assert rows[0].outcome is VerifyOutcome.PASS
    assert AuditEvent.VERIFICATION_COMPLETED in events
    assert audited[0].payload["outcome"] == "PASS"
    assert audited[0].payload["checks"][0]["verifier"] == "l0_result_present"
