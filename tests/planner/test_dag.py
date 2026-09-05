"""DAG constraints on planner output (FR-102)."""

from __future__ import annotations

import pytest

from vyomel.core.plan_spec import ActionSpec, HandwrittenPlan, StepSpec
from vyomel.core.types import ActionStatus
from vyomel.runtime.dag import ActionNode, CyclicPlanError, validate_acyclic


@pytest.mark.req("FR-102")
def test_linear_step_dependencies_materialize_to_action_edges() -> None:
    plan = HandwrittenPlan(
        steps=[
            StepSpec(
                alias="first",
                title="First",
                intent="start",
                actions=[ActionSpec(alias="a1", tool="task.report", parameters={"summary": "1"})],
            ),
            StepSpec(
                alias="second",
                title="Second",
                intent="finish",
                depends_on=["first"],
                actions=[ActionSpec(alias="a2", tool="task.report", parameters={"summary": "2"})],
            ),
        ]
    )
    nodes = [
        ActionNode(
            id="a1",
            status=ActionStatus.PLANNED,
            depends_on=(),
            step_id="first",
            tolerates_unverified=False,
        ),
        ActionNode(
            id="a2",
            status=ActionStatus.PLANNED,
            depends_on=("a1",),
            step_id="second",
            tolerates_unverified=False,
        ),
    ]
    validate_acyclic(nodes)
    assert len(plan.steps) == 2


@pytest.mark.req("FR-102")
def test_cycle_in_action_dependencies_is_rejected() -> None:
    nodes = [
        ActionNode(
            id="a",
            status=ActionStatus.PLANNED,
            depends_on=("b",),
            step_id="s",
            tolerates_unverified=False,
        ),
        ActionNode(
            id="b",
            status=ActionStatus.PLANNED,
            depends_on=("a",),
            step_id="s",
            tolerates_unverified=False,
        ),
    ]
    with pytest.raises(CyclicPlanError):
        validate_acyclic(nodes)
