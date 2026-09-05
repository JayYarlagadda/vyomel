"""Deterministic planner providers for tests and offline dev (FR-706)."""

from __future__ import annotations

import json
import re
from time import perf_counter

from vyomel.core.plan_spec import ActionSpec, HandwrittenPlan, StepSpec
from vyomel.models.types import ModelRequest, ModelResponse, ProviderInfo

_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/][^\s\"']+)|(?:\"([^\"]+)\")|(?:'([^']+)')")


class MockPlannerProvider:
    """Rule-based decomposition and replanning — no network, fully reproducible."""

    def __init__(self, *, variant: str = "v1") -> None:
        self._variant = variant
        self._info = ProviderInfo(
            name="mock-planner",
            is_remote=False,
            supports_structured_output=True,
            max_context=32_000,
        )

    @property
    def info(self) -> ProviderInfo:
        return self._info

    async def complete(self, req: ModelRequest) -> ModelResponse:
        started = perf_counter()
        if req.purpose == "planner.replan":
            plan = _recovery_plan()
        else:
            instruction = _instruction_from(req)
            plan = _plan_for_instruction(instruction.strip())
        payload = plan.model_dump(mode="json")
        latency_ms = (perf_counter() - started) * 1_000
        model_name = f"mock-planner-{self._variant}"
        return ModelResponse(
            content=json.dumps(payload),
            model=model_name,
            provider=self._info.name,
            prompt_tokens=max(1, sum(len(m.content) for m in req.messages) // 4),
            completion_tokens=max(1, len(json.dumps(payload)) // 4),
            latency_ms=latency_ms,
            parsed=payload,
            prompt_version=f"mock-{self._variant}",
        )


def _instruction_from(req: ModelRequest) -> str:
    for message in reversed(req.messages):
        if message.role == "user":
            body = message.content
            for marker in ("Original instruction:\n", "User instruction:\n"):
                if marker in body:
                    return body.split(marker, 1)[1].split("\n\n", 1)[0].strip()
            return body
    return ""


def _recovery_plan() -> HandwrittenPlan:
    return HandwrittenPlan(
        steps=[
            StepSpec(
                alias="recovery",
                title="Recover from failure",
                intent="Report outcome after replan",
                actions=[
                    ActionSpec(
                        alias="report",
                        tool="task.report",
                        parameters={"summary": "recovered after replan", "findings": []},
                    )
                ],
            )
        ]
    )


def _plan_for_instruction(instruction: str) -> HandwrittenPlan:
    lowered = instruction.lower()
    if "list" in lowered:
        path = _extract_path(instruction) or "."
        return HandwrittenPlan(
            steps=[
                StepSpec(
                    alias="survey",
                    title="List directory",
                    intent=f"List entries in {path}",
                    actions=[
                        ActionSpec(
                            alias="ls",
                            tool="fs.list_dir",
                            parameters={"path": path},
                        )
                    ],
                )
            ]
        )
    return HandwrittenPlan(
        steps=[
            StepSpec(
                alias="report",
                title="Summarize outcome",
                intent=instruction[:2_000],
                actions=[
                    ActionSpec(
                        alias="done",
                        tool="task.report",
                        parameters={"summary": instruction[:500], "findings": []},
                    )
                ],
            )
        ]
    )


def _extract_path(instruction: str) -> str | None:
    for match in _PATH_RE.finditer(instruction):
        candidate = match.group(0)
        if candidate.startswith(('"', "'")):
            candidate = candidate[1:-1]
        if "/" in candidate or "\\" in candidate or ":" in candidate:
            return candidate
    return None
