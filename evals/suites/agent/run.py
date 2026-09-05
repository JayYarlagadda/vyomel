"""Agent eval harness: task completion, tool-call accuracy, schema validity (docs/11)."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from vyomel.core.config import Settings
from vyomel.core.plan_spec import HandwrittenPlan
from vyomel.core.types import Capability
from vyomel.models.router import get_planner_provider
from vyomel.orchestrator.tools import ToolCatalog
from vyomel.planner.decompose import decompose
from vyomel.store.db import dispose_engine, init_engine
from vyomel.tools.registry import ToolRegistry, default_registry

TASKS = Path(__file__).resolve().parents[2] / "fixtures" / "agent" / "tasks.jsonl"


@dataclass(frozen=True, slots=True)
class AgentTask:
    instruction: str
    expected_tool: str
    expected_steps: int = 1
    expected_tools: tuple[str, ...] = ()


def load_tasks(path: Path) -> list[AgentTask]:
    items: list[AgentTask] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        tools = tuple(row.get("expected_tools") or [row["expected_tool"]])
        items.append(
            AgentTask(
                instruction=row["instruction"],
                expected_tool=row["expected_tool"],
                expected_steps=int(row.get("expected_steps", 1)),
                expected_tools=tools,
            )
        )
    return items


def _schema_valid(plan: HandwrittenPlan, registry: ToolRegistry) -> bool:
    try:
        HandwrittenPlan.model_validate(plan.model_dump(mode="json"))
    except ValidationError:
        return False
    for step in plan.steps:
        for action in step.actions:
            tool = registry.get(action.tool)
            if tool is None:
                return False
            try:
                tool.Input.model_validate(action.parameters)
            except ValidationError:
                return False
    return True


async def run_eval(settings: Settings) -> dict[str, float]:
    registry = default_registry()
    catalog = ToolCatalog(registry).list()
    provider = get_planner_provider(settings)
    tasks = load_tasks(TASKS)
    tool_hits = 0
    completed = 0
    schema_hits = 0
    multi_step_hits = 0
    multi_step_total = 0
    for task in tasks:
        result = await decompose(
            task.instruction,
            catalog=catalog,
            capability_ceiling=Capability.L2,
            settings=settings,
            registry=registry,
            provider=provider,
        )
        plan = result.plan
        if _schema_valid(plan, registry):
            schema_hits += 1
        tools_in_order = [a.tool for s in plan.steps for a in s.actions]
        primary = tools_in_order[0] if tools_in_order else ""
        if primary == task.expected_tool:
            tool_hits += 1
        step_ok = len(plan.steps) >= task.expected_steps
        tools_ok = True
        if task.expected_tools:
            tools_ok = tools_in_order[: len(task.expected_tools)] == list(task.expected_tools)
        if step_ok and tools_ok and primary == task.expected_tool:
            completed += 1
        if task.expected_steps >= 2:
            multi_step_total += 1
            if len(plan.steps) >= 2 and tools_ok:
                multi_step_hits += 1
    total = len(tasks) or 1
    metrics: dict[str, float] = {
        "task_completion_rate": completed / total,
        "tool_call_accuracy": tool_hits / total,
        "schema_validity_rate": schema_hits / total,
        "tasks": float(total),
    }
    if multi_step_total:
        metrics["multi_step_accuracy"] = multi_step_hits / multi_step_total
        metrics["multi_step_tasks"] = float(multi_step_total)
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Run agent planning eval.")
    parser.add_argument("--backend", choices=("mock", "mock-alt"), default="mock")
    args = parser.parse_args()
    settings = Settings(env="test", planner_backend=args.backend, workflow_store_backend="memory")
    init_engine(settings)
    try:
        metrics = asyncio.run(run_eval(settings))
    finally:
        asyncio.run(dispose_engine())
    print(json.dumps({"backend": args.backend, **metrics}, indent=2))
    if metrics["tool_call_accuracy"] < 0.8:
        return 1
    if metrics["schema_validity_rate"] < 0.98:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
