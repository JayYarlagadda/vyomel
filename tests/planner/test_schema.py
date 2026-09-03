"""Plan schema validation (FR-103)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from astra.core.plan_spec import ActionSpec, HandwrittenPlan, StepSpec


@pytest.mark.req("FR-103")
def test_valid_minimal_plan_parses() -> None:
    plan = HandwrittenPlan(
        steps=[
            StepSpec(
                alias="s1",
                title="Step",
                intent="Do something",
                actions=[ActionSpec(alias="a1", tool="task.report", parameters={"summary": "ok"})],
            )
        ]
    )
    assert plan.steps[0].actions[0].tool == "task.report"


@pytest.mark.req("FR-103")
def test_duplicate_step_aliases_rejected() -> None:
    with pytest.raises(ValidationError):
        HandwrittenPlan(
            steps=[
                StepSpec(
                    alias="dup",
                    title="A",
                    intent="a",
                    actions=[ActionSpec(alias="a1", tool="task.report", parameters={})],
                ),
                StepSpec(
                    alias="dup",
                    title="B",
                    intent="b",
                    actions=[ActionSpec(alias="a2", tool="task.report", parameters={})],
                ),
            ]
        )


@pytest.mark.req("FR-103")
def test_invalid_alias_pattern_rejected() -> None:
    with pytest.raises(ValidationError):
        ActionSpec(alias="Bad-Alias", tool="task.report", parameters={})
