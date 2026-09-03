"""Token budget gate (FR-108)."""

from __future__ import annotations

import pytest

from astra.core.errors import BudgetExceededError
from astra.core.plan_spec import ActionSpec, HandwrittenPlan, StepSpec
from astra.planner.budget import enforce_token_budget, estimate_plan_tokens


@pytest.mark.req("FR-108")
def test_estimate_grows_with_plan_size() -> None:
    small = HandwrittenPlan(
        steps=[
            StepSpec(
                alias="s",
                title="S",
                intent="i",
                actions=[ActionSpec(alias="a", tool="task.report", parameters={})],
            )
        ]
    )
    large = HandwrittenPlan(
        steps=[
            StepSpec(
                alias=f"s{i}",
                title="S",
                intent="i" * 200,
                actions=[
                    ActionSpec(
                        alias=f"a{i}",
                        tool="task.report",
                        parameters={"summary": "x" * 100},
                    )
                ],
            )
            for i in range(20)
        ]
    )
    assert estimate_plan_tokens(large).total_tokens > estimate_plan_tokens(small).total_tokens


@pytest.mark.req("FR-108")
def test_enforce_refuses_oversized_plan() -> None:
    plan = HandwrittenPlan(
        steps=[
            StepSpec(
                alias=f"s{i}",
                title="S",
                intent="i",
                actions=[ActionSpec(alias=f"a{i}", tool="task.report", parameters={})],
            )
            for i in range(50)
        ]
    )
    with pytest.raises(BudgetExceededError):
        enforce_token_budget(plan, token_budget=10)
