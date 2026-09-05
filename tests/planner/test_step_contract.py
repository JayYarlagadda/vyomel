"""Step contract fields on plan wire format (FR-105)."""

from __future__ import annotations

import pytest

from vyomel.core.plan_spec import ActionSpec, HandwrittenPlan, StepSpec
from vyomel.core.types import Capability


@pytest.mark.req("FR-105")
def test_action_contract_fields_round_trip() -> None:
    plan = HandwrittenPlan(
        steps=[
            StepSpec(
                alias="write",
                title="Write",
                intent="persist",
                required_capability=Capability.L1,
                actions=[
                    ActionSpec(
                        alias="w",
                        tool="task.report",
                        parameters={"summary": "ok"},
                        postconditions=[{"type": "file_exists", "path": "x"}],
                        timeout_s=30,
                        max_retries=1,
                    )
                ],
            )
        ]
    )
    action = plan.steps[0].actions[0]
    assert action.timeout_s == 30
    assert action.max_retries == 1
    assert action.postconditions[0]["type"] == "file_exists"
