"""API request and response models.

Separate from the ORM models on purpose: the wire contract is versioned and
must not change just because a column was renamed.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from astra.core.types import (
    ActionStatus,
    ApprovalStatus,
    Capability,
    Decision,
    StepStatus,
    TaskOrigin,
    TaskStatus,
)
from astra.orchestrator.plans import HandwrittenPlan


class TaskBoundsIn(BaseModel):
    max_wall_clock_s: Annotated[int, Field(ge=1)] | None = None
    max_token_budget: Annotated[int, Field(ge=1)] | None = None


class CreateTaskRequest(BaseModel):
    instruction: Annotated[str, Field(min_length=1, max_length=8_000)]
    context_hints: dict[str, Any] = Field(default_factory=dict)
    # The user's up-front consent boundary. Nothing discovered during execution
    # may raise it -- see docs/06-SECURITY-PERMISSIONS.md section 5.
    capability_ceiling: Capability = Capability.L2
    bounds: TaskBoundsIn | None = None
    autostart: bool = True
    dry_run: bool = False
    origin: TaskOrigin = TaskOrigin.API
    plan: HandwrittenPlan | None = None


class TaskProgress(BaseModel):
    steps_total: int = 0
    steps_done: int = 0
    actions_total: int = 0
    actions_done: int = 0


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    instruction: str
    status: TaskStatus
    origin: TaskOrigin
    capability_ceiling: Capability
    plan_version: int
    replan_count: int
    tokens_used: int
    cost_usd: Decimal
    trace_id: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    deadline_at: datetime | None = None
    progress: TaskProgress = Field(default_factory=TaskProgress)


class StepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    ordinal: int
    title: str
    intent: str
    status: StepStatus
    depends_on: list[str]
    tolerates_unverified: bool


class ActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    step_id: str
    tool: str
    status: ActionStatus
    capability_level: Capability
    attempt_count: int
    depends_on: list[str]
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class PlanResponse(BaseModel):
    task_id: str
    plan_version: int
    steps: list[StepResponse]
    actions: list[ActionResponse]


class CancelRequest(BaseModel):
    compensate: bool = True


class IrreversibleEffectResponse(BaseModel):
    action_id: str
    tool: str
    summary: str


class CompensationFailureResponse(BaseModel):
    action_id: str
    tool: str
    error: str


class CancelResponse(BaseModel):
    task_id: str
    status: TaskStatus
    compensated: list[str]
    irreversible: list[IrreversibleEffectResponse]
    still_running: list[str]
    failed: list[CompensationFailureResponse]


class TaskListResponse(BaseModel):
    items: list[TaskResponse]
    next_cursor: str | None = None


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    action_id: str
    capability_level: Capability
    status: ApprovalStatus
    summary: str
    presented: dict[str, Any]
    blast_radius: dict[str, Any]
    policy_rule_id: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    consumed_at: datetime | None = None
    expires_at: datetime
    created_at: datetime


class ApprovalListResponse(BaseModel):
    items: list[ApprovalResponse]
    next_cursor: str | None = None


class DecideApprovalRequest(BaseModel):
    """One endpoint for all three answers, because they are one decision.

    ``MODIFIED`` carries replacement parameters; they are re-validated against
    the tool schema and re-classified before the approval is recorded, so an
    edit cannot be used to slip a higher-capability action past the gate
    (docs/06-SECURITY-PERMISSIONS.md section 4.2).
    """

    decision: Literal["APPROVED", "MODIFIED", "REJECTED"]
    parameters: dict[str, Any] | None = None
    reason: Annotated[str, Field(max_length=2_000)] | None = None
    decided_by: Annotated[str, Field(min_length=1, max_length=200)] = "user:local"

    @model_validator(mode="after")
    def _parameters_only_for_modified(self) -> DecideApprovalRequest:
        if self.decision == "MODIFIED" and not self.parameters:
            raise ValueError("a MODIFIED decision must carry replacement parameters")
        if self.decision != "MODIFIED" and self.parameters is not None:
            raise ValueError("parameters may only accompany a MODIFIED decision")
        return self


class AuditRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    occurred_at: datetime
    actor: str
    event_type: str
    task_id: str | None = None
    action_id: str | None = None
    capability_level: Capability | None = None
    payload: dict[str, Any]
    hash: str


class AuditListResponse(BaseModel):
    items: list[AuditRecordResponse]


class ChainReportResponse(BaseModel):
    ok: bool
    rows: int
    first_divergence_id: int | None = None
    detail: str | None = None


class PolicyRuleResponse(BaseModel):
    id: str
    decision: Decision
    tool: str | None = None
    level: Capability | None = None
    max_level: Capability | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None
    expires: date | None = None


class PolicyResponse(BaseModel):
    version: int
    policy_hash: str
    source: str | None = None
    defaults: dict[Capability, Decision]
    rules: list[PolicyRuleResponse]
    egress_deny_by_default: bool
    egress_allow_domains: list[str]


class PolicyTestRequest(BaseModel):
    tool: Annotated[str, Field(min_length=1)]
    parameters: dict[str, Any] = Field(default_factory=dict)
    # Optional: when omitted the tool's own classification is used, which is
    # what production does. Supplying it tests a hypothetical.
    capability_level: Capability | None = None
    workflow: str | None = None


class PolicyTestResponse(BaseModel):
    tool: str
    capability_level: Capability
    escalation_reasons: list[str]
    decision: Decision
    rule_id: str
    reason: str
    policy_version: int
    policy_hash: str


class HealthResponse(BaseModel):
    status: str
    version: str


class ReadinessCheck(BaseModel):
    name: str
    ok: bool
    detail: str | None = None
    latency_ms: float | None = None


class ReadinessResponse(BaseModel):
    ready: bool
    checks: list[ReadinessCheck]


class VersionResponse(BaseModel):
    version: str
    schema_revision: str | None = None
    environment: str


class ToolCatalogItem(BaseModel):
    name: str
    version: str
    description: str
    base_capability: Capability
    reversible: bool
    idempotent: bool
    actuation_tier: int
    concurrency_key: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)


class ToolListResponse(BaseModel):
    items: list[ToolCatalogItem]


class InvokeToolRequest(BaseModel):
    parameters: dict[str, Any] = Field(default_factory=dict)


class InvokeToolResponse(BaseModel):
    invoke_id: str
    tool: str
    capability_level: Capability
    decision: Decision
    result: dict[str, Any]
