"""Bounded replanning after step failure (FR-106)."""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from vyomel.core.config import Settings
from vyomel.core.errors import VyomelError, ErrorCode
from vyomel.core.plan_spec import HandwrittenPlan
from vyomel.core.types import Capability, Trust
from vyomel.models.providers.protocol import ModelProvider
from vyomel.models.router import get_planner_provider
from vyomel.models.types import ChatMessage, ModelRequest
from vyomel.planner.catalog import catalog_for_prompt, filter_catalog
from vyomel.planner.decompose import MAX_SCHEMA_RETRIES, _validate_tools
from vyomel.prompts.boundaries import wrap_untrusted
from vyomel.prompts.loader import load_prompt
from vyomel.tools.catalog import CatalogEntry
from vyomel.tools.registry import ToolRegistry


class ReplanError(VyomelError):
    code = ErrorCode.INVALID_PARAMETERS


@dataclass(frozen=True, slots=True)
class ReplanResult:
    plan: HandwrittenPlan
    model: str
    provider: str
    prompt_hash: str


async def replan(
    *,
    instruction: str,
    failed_step: str,
    error: str,
    observation: str,
    catalog: list[CatalogEntry],
    capability_ceiling: Capability,
    settings: Settings,
    registry: ToolRegistry,
    provider: ModelProvider | None = None,
) -> ReplanResult:
    scoped = filter_catalog(catalog, ceiling=capability_ceiling)
    prompt = load_prompt("planner", "replan.v1")
    tools_json = json.dumps(catalog_for_prompt(scoped), indent=2, sort_keys=True)
    user_body = prompt.body.format(
        failed_step=failed_step,
        error=wrap_untrusted(error, source="runtime", trust=Trust.TOOL_UNTRUSTED),
        observation=wrap_untrusted(observation, source="runtime", trust=Trust.TOOL_UNTRUSTED),
        instruction=instruction,
        tools_json=tools_json,
    )
    model = provider or get_planner_provider(settings)

    last_error: str | None = None
    for _attempt in range(MAX_SCHEMA_RETRIES + 1):
        response = await model.complete(
            ModelRequest(
                purpose="planner.replan",
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
                last_error = str(exc)
                continue
        try:
            plan = HandwrittenPlan.model_validate(raw)
        except ValidationError as exc:
            last_error = str(exc)
            continue
        _validate_tools(plan, registry=registry, allowed={entry.name for entry in scoped})
        return ReplanResult(
            plan=plan,
            model=response.model,
            provider=response.provider,
            prompt_hash=prompt.content_hash,
        )
    raise ReplanError(f"replan failed schema validation: {last_error}")
