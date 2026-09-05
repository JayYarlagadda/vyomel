"""Handwritten research DAGs for long-run durability evals (M6)."""

from __future__ import annotations

from vyomel.core.plan_spec import ActionSpec, HandwrittenPlan, StepSpec


def build_research_plan(*, items: int) -> HandwrittenPlan:
    """Fan-out ``items`` mock fetches, then a terminal report."""
    if items < 1:
        raise ValueError("items must be >= 1")
    fetches = [
        ActionSpec(
            alias=f"fetch_{index:03d}",
            tool="web.fetch_mock",
            parameters={"url": f"https://mock.vyomel/research/{index:03d}"},
        )
        for index in range(items)
    ]
    report_deps = [action.alias for action in fetches]
    return HandwrittenPlan(
        steps=[
            StepSpec(
                alias="research",
                title="Collect mock research pages",
                intent=f"Fetch {items} deterministic pages",
                actions=fetches,
            ),
            StepSpec(
                alias="report",
                title="Summarize research",
                intent="Record completion",
                depends_on=["research"],
                actions=[
                    ActionSpec(
                        alias="done",
                        tool="task.report",
                        parameters={
                            "summary": f"Collected {items} mock research pages.",
                            "findings": report_deps[:5],
                        },
                        depends_on=report_deps,
                    )
                ],
            ),
        ]
    )
