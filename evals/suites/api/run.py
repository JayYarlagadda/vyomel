"""Scenario S3 eval (docs/00 §S3, M9 exit): interview email → calendar → prep blocks.

Every L3 action must evaluate to CONFIRM under the shipped policy. The eval
records the gate, then continues as if the operator approved — it does not
bypass classification.
"""

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

from vyomel.core.cancel import CancellationToken
from vyomel.core.clock import FrozenClock
from vyomel.core.config import Settings
from vyomel.core.types import Capability, Decision
from vyomel.security.capability import Invocation, classify
from vyomel.security.policy import PolicyRequest, load_policy, variables_for
from vyomel.tools.api.session import login_fixture, reset_api_sessions
from vyomel.tools.base import Tool, ToolContext
from vyomel.tools.registry import default_registry

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def _ctx(settings: Settings) -> ToolContext:
    return ToolContext(
        task_id="s3-eval",
        action_id="a1",
        capability_granted=Capability.L3,
        scratch_dir=settings.scratch_dir,
        allowed_roots=[settings.workspace_root],
        deadline=NOW + timedelta(hours=2),
        cancel=CancellationToken(),
        clock=FrozenClock(NOW),
        trash_dir=settings.trash_dir,
        settings=settings,
    )


def _gate(tool: Tool, params, policy) -> dict[str, str]:
    parsed_params = params.model_dump(mode="json")
    classification = classify(
        Invocation(
            tool=tool.name,
            parameters=parsed_params,
            base=tool.classify(params),
            actuation_tier=tool.actuation_tier,
        ),
        policy.escalation,
    )
    decision = policy.evaluate(
        PolicyRequest(tool=tool.name, level=classification.level, parameters=parsed_params)
    )
    return {
        "tool": tool.name,
        "level": classification.level.value,
        "decision": decision.decision.value,
        "rule_id": decision.rule_id,
    }


async def run_s3(settings: Settings) -> dict[str, object]:
    reset_api_sessions()
    settings.ensure_directories()
    login_fixture(settings, "google")
    registry = default_registry()
    ctx = _ctx(settings)
    policy = load_policy(
        Path("config/policy.yaml"),
        variables=variables_for(settings.scratch_dir, settings.workspace_root),
    )

    gates: list[dict[str, str]] = []
    l3_actions = 0
    l3_confirm = 0
    l3_allow = 0

    async def step(name: str, payload: dict) -> object:
        nonlocal l3_actions, l3_confirm, l3_allow
        tool = registry.get(name)
        params = tool.Input.model_validate(payload)
        gate = _gate(tool, params, policy)
        gates.append(gate)
        if gate["level"] in {"L3", "L4"}:
            l3_actions += 1
            if gate["decision"] == Decision.CONFIRM.value:
                l3_confirm += 1
            if gate["decision"] == Decision.ALLOW.value:
                l3_allow += 1
            if gate["decision"] == Decision.DENY.value:
                raise RuntimeError(f"{name} denied by policy: {gate}")
        elif gate["decision"] == Decision.DENY.value:
            raise RuntimeError(f"{name} denied by policy: {gate}")
        return await tool.execute(params, ctx)

    search = await step("email.search", {"query": "interview"})
    interview = next(m for m in search.messages if "Interview" in m.subject)
    read = await step("email.read", {"message_id": interview.id})
    if "jordan@acme.test" not in read.body:
        raise RuntimeError("interview email did not name the interviewer")

    interview_day = (NOW + timedelta(days=1)).isoformat()
    listed = await step("calendar.list", {"day": interview_day})
    if not listed.events:
        raise RuntimeError("expected existing busy events on interview day")

    free = await step(
        "calendar.find_free",
        {"day": interview_day, "duration_minutes": 60, "count": 2},
    )
    if len(free.slots) != 2:
        raise RuntimeError(f"expected 2 prep slots, got {len(free.slots)}")

    created_ids: list[str] = []
    for index, slot in enumerate(free.slots, start=1):
        created = await step(
            "calendar.create_event",
            {
                "title": f"Interview prep {index}",
                "start": slot.start,
                "end": slot.end,
                "attendees": ["jordan@acme.test"],
            },
        )
        created_ids.append(created.id)

    success = (
        l3_actions >= 2 and l3_allow == 0 and l3_confirm == l3_actions and len(created_ids) == 2
    )
    return {
        "scenario": "S3",
        "success": success,
        "interview_email_id": interview.id,
        "interview_from": read.from_addr,
        "prep_event_ids": created_ids,
        "l3_actions": l3_actions,
        "l3_confirm": l3_confirm,
        "l3_auto_allowed": l3_allow,
        "gates": gates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scenario S3 (API tools).")
    parser.parse_args()
    settings = Settings(env="test", log_format="json", oauth_backend="memory")
    metrics = asyncio.run(run_s3(settings))
    print(json.dumps(metrics, indent=2))
    if not metrics["success"]:
        return 1
    if int(metrics["l3_auto_allowed"]) != 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
