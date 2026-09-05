"""Trusted workflow ceiling (FR-310)."""

from __future__ import annotations

import pytest
from sqlalchemy import CheckConstraint

from vyomel.core.types import Capability
from vyomel.learning.proposal import WorkflowProposal, WorkflowStepTemplate
from vyomel.learning.store import MemoryWorkflowStore, accept_workflow
from vyomel.store.models import Workflow


@pytest.mark.req("FR-310")
def test_accept_rejects_trust_above_l2() -> None:
    store = MemoryWorkflowStore()
    proposal = WorkflowProposal(
        id="01TESTWORKFLOW000000000001",
        name="bad",
        description="should not accept",
        definition=[
            WorkflowStepTemplate(alias="a1", tool="email.send", parameters={}),
            WorkflowStepTemplate(alias="a2", tool="email.send", parameters={}),
            WorkflowStepTemplate(alias="a3", tool="email.send", parameters={}),
        ],
        parameters=[],
        occurrence_count=3,
        trust_level=Capability.L3,
        status="proposed",
        pattern_key="email.send|email.send|email.send",
    )
    store.put(proposal)
    with pytest.raises(Exception) as exc:
        accept_workflow(store, proposal.id)
    assert "L2" in str(exc.value)


@pytest.mark.req("FR-310")
def test_orm_check_constraint_caps_trust_at_l2() -> None:
    checks = [
        c
        for c in Workflow.__table__.constraints
        if isinstance(c, CheckConstraint)
    ]
    assert any("L2" in str(c.sqltext) for c in checks)
