"""Policy endpoints.

``/test`` is the important one. A policy you cannot ask questions of is a policy
you find out about in production: it answers "what would Astra do with this
exact invocation" without running it, and it reports the escalation reasons, so
a surprising deny is traceable to the rule that caused it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import ValidationError

from astra.api.schemas import (
    PolicyResponse,
    PolicyRuleResponse,
    PolicyTestRequest,
    PolicyTestResponse,
)
from astra.core.config import Settings, get_settings
from astra.core.errors import ConfigError
from astra.core.types import Trust
from astra.orchestrator.runtime import get_registry
from astra.security.capability import Invocation, classify
from astra.security.policy import Policy, PolicyRequest, store_for

router = APIRouter(prefix="/v1/policy", tags=["policy"])


def _to_response(policy: Policy) -> PolicyResponse:
    return PolicyResponse(
        version=policy.version,
        policy_hash=policy.hash,
        source=str(policy.source) if policy.source else None,
        defaults=dict(policy.defaults),
        rules=[
            PolicyRuleResponse(
                id=rule.id,
                decision=rule.decision,
                tool=rule.match.tool,
                level=rule.level,
                max_level=rule.max_level,
                args=dict(rule.match.args),
                reason=rule.reason,
                expires=rule.expires,
            )
            for rule in policy.rules
        ],
        egress_deny_by_default=policy.egress.deny_by_default,
        egress_allow_domains=list(policy.egress.allow_domains),
    )


@router.get("", response_model=PolicyResponse)
async def show_policy(
    settings: Annotated[Settings, Depends(get_settings)],
) -> PolicyResponse:
    return _to_response(store_for(settings).get())


@router.post("/reload", response_model=PolicyResponse)
async def reload_policy(
    settings: Annotated[Settings, Depends(get_settings)],
) -> PolicyResponse:
    return _to_response(store_for(settings).reload())


@router.post("/test", response_model=PolicyTestResponse)
async def test_policy(
    payload: PolicyTestRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> PolicyTestResponse:
    # RegistryError already carries NOT_FOUND, so an unknown tool surfaces as a
    # 404 without translation here.
    tool = get_registry().get(payload.tool)
    try:
        parsed = tool.Input.model_validate(payload.parameters)
    except ValidationError as exc:
        raise ConfigError(
            f"parameters are not valid for {payload.tool}",
            detail={"errors": exc.errors(include_url=False)},
        ) from exc

    parameters = parsed.model_dump(mode="json")
    policy = store_for(settings).get()
    classification = classify(
        Invocation(
            tool=tool.name,
            parameters=parameters,
            base=payload.capability_level or tool.classify(parsed),
            actuation_tier=tool.actuation_tier,
            trust=Trust.USER,
        ),
        policy.escalation,
    )
    decision = policy.evaluate(
        PolicyRequest(
            tool=tool.name,
            level=classification.level,
            parameters=parameters,
            workflow=payload.workflow,
        )
    )
    return PolicyTestResponse(
        tool=tool.name,
        capability_level=classification.level,
        escalation_reasons=list(classification.reasons),
        decision=decision.decision,
        rule_id=decision.rule_id,
        reason=decision.reason,
        policy_version=decision.policy_version,
        policy_hash=decision.policy_hash,
    )
