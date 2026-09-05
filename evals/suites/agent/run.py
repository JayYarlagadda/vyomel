"""Agent eval harness: task completion and tool-call accuracy (docs/11-EVALUATION.md)."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from vyomel.core.config import Settings
from vyomel.core.types import Capability
from vyomel.models.router import get_planner_provider
from vyomel.orchestrator.tools import ToolCatalog
from vyomel.planner.decompose import decompose
from vyomel.store.db import dispose_engine, init_engine
from vyomel.tools.registry import default_registry

TASKS = Path(__file__).resolve().parents[2] / "fixtures" / "agent" / "tasks.jsonl"


@dataclass(frozen=True, slots=True)
class AgentTask:
    instruction: str
    expected_tool: str


def load_tasks(path: Path) -> list[AgentTask]:
    items: list[AgentTask] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        items.append(AgentTask(instruction=row["instruction"], expected_tool=row["expected_tool"]))
    return items


async def run_eval(settings: Settings) -> dict[str, float]:
    registry = default_registry()
    catalog = ToolCatalog(registry).list()
    provider = get_planner_provider(settings)
    tasks = load_tasks(TASKS)
    tool_hits = 0
    completed = 0
    for task in tasks:
        result = await decompose(
            task.instruction,
            catalog=catalog,
            capability_ceiling=Capability.L2,
            settings=settings,
            registry=registry,
            provider=provider,
        )
        tool = result.plan.steps[0].actions[0].tool
        if tool == task.expected_tool:
            tool_hits += 1
            completed += 1
    total = len(tasks) or 1
    return {
        "task_completion_rate": completed / total,
        "tool_call_accuracy": tool_hits / total,
        "tasks": float(total),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run agent planning eval.")
    parser.add_argument("--backend", choices=("mock", "mock-alt"), default="mock")
    args = parser.parse_args()
    settings = Settings(env="test", planner_backend=args.backend)
    init_engine(settings)
    try:
        metrics = asyncio.run(run_eval(settings))
    finally:
        asyncio.run(dispose_engine())
    print(json.dumps({"backend": args.backend, **metrics}, indent=2))
    if metrics["tool_call_accuracy"] < 0.8:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
