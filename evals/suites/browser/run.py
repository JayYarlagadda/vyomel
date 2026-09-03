"""Browser workflow eval (docs/11-EVALUATION.md §5, M7 exit criteria)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from astra.core.cancel import CancellationToken
from astra.core.clock import SystemClock
from astra.core.config import Settings
from astra.core.types import Capability
from astra.tools.base import ToolContext
from astra.tools.browser.metrics import actuation_tier_distribution, reset_actuation_tiers
from astra.tools.browser.session import reset_sessions
from astra.tools.registry import default_registry
from evals.suites.browser.workflows import Workflow, build_workflows


async def run_workflow(workflow: Workflow, settings: Settings, registry, scratch: Path) -> bool:
    reset_sessions()
    ctx = ToolContext(
        task_id=f"browser-eval-{workflow.name}",
        action_id="a1",
        capability_granted=Capability.L3,
        scratch_dir=scratch,
        allowed_roots=[scratch],
        deadline=datetime.now(UTC) + timedelta(hours=1),
        cancel=CancellationToken(),
        clock=SystemClock(),
        trash_dir=scratch / "trash",
        settings=settings,
    )
    last: dict = {}
    for step in workflow.steps:
        tool = registry.get(step.tool)
        params = tool.Input.model_validate(step.parameters)
        output = await tool.execute(params, ctx)
        last = output.model_dump(mode="json")
    value = last.get(workflow.expect_field)
    if workflow.expect_field == "title" and isinstance(value, str):
        return workflow.expect_value.lower() in value.lower()
    if workflow.expect_field == "bytes":
        return int(value or 0) >= int(workflow.expect_value)
    return value == workflow.expect_value


async def run_eval(settings: Settings) -> dict[str, object]:
    registry = default_registry()
    scratch = settings.scratch_dir
    scratch.mkdir(parents=True, exist_ok=True)
    reset_actuation_tiers()
    workflows = build_workflows()
    passed = 0
    failures: list[str] = []
    for workflow in workflows:
        ok = await run_workflow(workflow, settings, registry, scratch)
        if ok:
            passed += 1
        else:
            failures.append(workflow.name)
    total = len(workflows)
    success_rate = passed / total if total else 0.0
    tiers = actuation_tier_distribution()
    return {
        "workflows": total,
        "passed": passed,
        "success_rate": success_rate,
        "actuation_tier_distribution": tiers,
        "failures": failures,
        "backend": settings.browser_backend,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run browser workflow eval.")
    parser.add_argument("--backend", choices=("fixture", "playwright", "auto"), default="fixture")
    args = parser.parse_args()
    settings = Settings(env="test", log_format="json", browser_backend=args.backend)
    metrics = asyncio.run(run_eval(settings))
    print(json.dumps(metrics, indent=2))
    if float(metrics["success_rate"]) < 0.8:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
