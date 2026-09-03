"""Research plan builder for long-run evals."""

from __future__ import annotations

from astra.planner.longrun import build_research_plan


def test_research_plan_action_count() -> None:
    plan = build_research_plan(items=100)
    actions = [action for step in plan.steps for action in step.actions]
    assert len(actions) == 101
    assert sum(1 for action in actions if action.tool == "web.fetch_mock") == 100
