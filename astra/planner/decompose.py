"""Natural-language decomposition (FR-102, FR-103)."""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from astra.core.config import Settings
from astra.core.errors import AstraError, ErrorCode
from astra.core.plan_spec import HandwrittenPlan
from astra.core.types import Capability
from astra.models.providers.protocol import ModelProvider
from astra.models.router import get_planner_provider
from astra.models.types import ChatMessage, ModelRequest
from astra.planner.catalog import catalog_for_prompt, filter_catalog
from astra.prompts.loader import load_prompt
from astra.tools.catalog import CatalogEntry
from astra.tools.registry import ToolRegistry

MAX_SCHEMA_RETRIES = 2


class PlannerError(AstraError):
    code = ErrorCode.INVALID_PARAMETERS


@dataclass(frozen=True, slots=True)
class DecomposeResult:
    plan: HandwrittenPlan
    normalized_intent: str
    model: str
    provider: str
    prompt_hash: str
    prompt_version: str


async def decompose(
    instruction: str,
    *,
    catalog: list[CatalogEntry],
    capability_ceiling: Capability,
    settings: Settings,
    registry: ToolRegistry,
    provider: ModelProvider | None = None,
) -> DecomposeResult:
    scoped = filter_catalog(catalog, ceiling=capability_ceiling)
    if not scoped:
        raise PlannerError("no tools available under the task capability ceiling")

    prompt = load_prompt("planner", "decompose.v1")
    tools_json = json.dumps(catalog_for_prompt(scoped), indent=2, sort_keys=True)
    user_body = prompt.body.format(instruction=instruction.strip(), tools_json=tools_json)
    model = provider or get_planner_provider(settings)

    last_error: str | None = None
    for _attempt in range(MAX_SCHEMA_RETRIES + 1):
        response = await model.complete(
            ModelRequest(
                purpose="planner.decompose",
                messages=(
                    ChatMessage(role="system", content="You output only valid JSON plans."),
                    ChatMessage(role="user", content=user_body),
                ),
                json_schema=HandwrittenPlan.model_json_schema(),
                temperature=0.0,
                seed=0 if settings.env == "test" else None,
            )
        )
        raw = response.parsed
        if raw is None:
            try:
                raw = json.loads(response.content)
            except json.JSONDecodeError as exc:
                last_error = f"model returned non-JSON: {exc}"
                continue
        try:
            plan = HandwrittenPlan.model_validate(raw)
        except ValidationError as exc:
            last_error = str(exc)
            continue
        _validate_tools(plan, registry=registry, allowed={entry.name for entry in scoped})
        return DecomposeResult(
            plan=plan,
            normalized_intent=instruction.strip(),
            model=response.model,
            provider=response.provider,
            prompt_hash=prompt.content_hash,
            prompt_version=prompt.version,
        )

    raise PlannerError(
        f"plan failed schema validation after {MAX_SCHEMA_RETRIES + 1} attempts: {last_error}"
    )


def _validate_tools(
    plan: HandwrittenPlan,
    *,
    registry: ToolRegistry,
    allowed: set[str],
) -> None:
    for step in plan.steps:
        for action in step.actions:
            if action.tool not in allowed:
                raise PlannerError(
                    f"action {action.alias} uses {action.tool!r}, which is not in the "
                    "capability-filtered catalog"
                )
            registry.get(action.tool)
