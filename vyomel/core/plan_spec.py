"""Plan wire format shared by the planner and orchestrator (FR-102, FR-107)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from vyomel.core.types import Capability


class ActionSpec(BaseModel):
    alias: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    tool: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    preconditions: dict[str, Any] = Field(default_factory=dict)
    postconditions: list[dict[str, Any]] = Field(default_factory=list)
    timeout_s: int | None = Field(default=None, ge=1)
    max_retries: int | None = Field(default=None, ge=0)


class StepSpec(BaseModel):
    alias: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1, max_length=200)
    intent: str = Field(min_length=1, max_length=2_000)
    actions: list[ActionSpec] = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    tolerates_unverified: bool = False
    required_capability: Capability | None = None


class HandwrittenPlan(BaseModel):
    steps: list[StepSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_aliases(self) -> HandwrittenPlan:
        step_aliases = [s.alias for s in self.steps]
        if len(step_aliases) != len(set(step_aliases)):
            raise ValueError("step aliases must be unique")
        action_aliases = [a.alias for s in self.steps for a in s.actions]
        if len(action_aliases) != len(set(action_aliases)):
            raise ValueError("action aliases must be unique across the plan")
        return self
