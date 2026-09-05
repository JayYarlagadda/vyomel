"""Token and cost estimation before execution (FR-108)."""

from __future__ import annotations

from dataclasses import dataclass

from vyomel.core.errors import BudgetExceededError
from vyomel.core.plan_spec import HandwrittenPlan


@dataclass(frozen=True, slots=True)
class PlanBudgetEstimate:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


def estimate_plan_tokens(
    plan: HandwrittenPlan, *, instruction_chars: int = 0
) -> PlanBudgetEstimate:
    """Heuristic estimate: chars/4 per action plus instruction overhead."""
    prompt = max(1, instruction_chars // 4) + 200
    completion = 0
    for step in plan.steps:
        for action in step.actions:
            prompt += 40 + len(action.tool) + len(str(action.parameters)) // 4
            completion += 80
    total = prompt + completion
    return PlanBudgetEstimate(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
    )


def enforce_token_budget(
    plan: HandwrittenPlan,
    *,
    token_budget: int,
    instruction_chars: int = 0,
) -> PlanBudgetEstimate:
    estimate = estimate_plan_tokens(plan, instruction_chars=instruction_chars)
    if estimate.total_tokens > token_budget:
        raise BudgetExceededError(
            f"plan estimate {estimate.total_tokens} tokens exceeds budget {token_budget}",
            detail={"estimate": estimate.total_tokens, "budget": token_budget},
        )
    return estimate
