"""Desktop workflow eval (docs/11-EVALUATION.md §4, M8 exit criteria)."""

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

from evals.suites.desktop.workflows import (
    VerificationFault,
    Workflow,
    build_workflows,
    verification_fault_workflows,
)

from vyomel.core.cancel import CancellationToken
from vyomel.core.clock import SystemClock
from vyomel.core.config import Settings
from vyomel.core.types import Capability
from vyomel.tools.base import ToolContext
from vyomel.tools.desktop.metrics import (
    actuation_tier_distribution,
    reset_actuation_tiers,
    vision_tier_ratio,
)
from vyomel.tools.desktop.session import reset_sessions
from vyomel.tools.registry import default_registry


async def run_workflow(workflow: Workflow, settings: Settings, registry, scratch: Path) -> bool:
    reset_sessions()
    ctx = ToolContext(
        task_id=f"desktop-eval-{workflow.name}",
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
    if workflow.expect_field == "windows" and isinstance(value, list):
        return any(workflow.expect_value in window for window in value)
    if workflow.expect_field == "title" and isinstance(value, str):
        return workflow.expect_value.lower() in value.lower()
    return value == workflow.expect_value


def _verification_catches_fault(fault: VerificationFault, result) -> bool:
    actual = getattr(result, fault.field, None)
    return actual != fault.correct_value


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

    vf_total = 0
    vf_caught = 0
    for fault in verification_fault_workflows():
        reset_sessions()
        ctx = ToolContext(
            task_id=f"desktop-vf-{fault.name}",
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
        result = None
        for step in fault.steps:
            tool = registry.get(step.tool)
            params = tool.Input.model_validate(step.parameters)
            result = await tool.execute(params, ctx)
        assert result is not None
        vf_total += 1
        if _verification_catches_fault(fault, result):
            vf_caught += 1

    total = len(workflows)
    success_rate = passed / total if total else 0.0
    verification_catch_rate = vf_caught / vf_total if vf_total else 1.0
    tiers = actuation_tier_distribution()
    vision_ratio = vision_tier_ratio()
    return {
        "workflows": total,
        "passed": passed,
        "success_rate": success_rate,
        "verification_catch_rate": verification_catch_rate,
        "vision_tier_ratio": vision_ratio,
        "actuation_tier_distribution": tiers,
        "failures": failures,
        "backend": settings.desktop_backend,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run desktop workflow eval.")
    parser.add_argument("--backend", choices=("fixture", "uia", "auto"), default="fixture")
    args = parser.parse_args()
    settings = Settings(env="test", log_format="json", desktop_backend=args.backend)
    metrics = asyncio.run(run_eval(settings))
    print(json.dumps(metrics, indent=2))
    if float(metrics["success_rate"]) < 0.7:
        return 1
    if float(metrics["verification_catch_rate"]) < 1.0:
        return 1
    if float(metrics["vision_tier_ratio"]) >= 0.3:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
