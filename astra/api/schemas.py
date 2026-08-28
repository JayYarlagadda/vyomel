"""API request and response models.

Separate from the ORM models on purpose: the wire contract is versioned and
must not change just because a column was renamed.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from astra.core.types import Capability, TaskOrigin, TaskStatus


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


class TaskListResponse(BaseModel):
    items: list[TaskResponse]
    next_cursor: str | None = None


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
