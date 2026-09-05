"""``workflow.invoke`` — run an accepted learned/saved workflow (docs/05 §3.7)."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from vyomel.core.errors import ErrorCode, ToolError
from vyomel.core.types import Capability
from vyomel.learning.store import (
    WorkflowError,
    WorkflowNotFoundError,
    expand_workflow,
    get_workflow_store,
    require_accepted,
)
from vyomel.tools.base import Tool, ToolContext


class WorkflowInvokeInput(BaseModel):
    workflow_id: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


class WorkflowInvokeOutput(BaseModel):
    workflow_id: str
    name: str
    trust_level: Capability
    steps: list[dict[str, Any]]


class WorkflowInvoke(Tool):
    name: ClassVar[str] = "workflow.invoke"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Expand an accepted saved/learned workflow with parameters. "
        "Unaccepted proposals are refused."
    )
    Input: ClassVar[type[BaseModel]] = WorkflowInvokeInput
    Output: ClassVar[type[BaseModel]] = WorkflowInvokeOutput
    base_capability: ClassVar[Capability] = Capability.L1
    reversible: ClassVar[bool] = False
    idempotent: ClassVar[bool] = True
    concurrency_key: ClassVar[str] = "workflow"

    def classify(self, params: BaseModel) -> Capability:
        assert isinstance(params, WorkflowInvokeInput)
        store = get_workflow_store()
        proposal = store.get(params.workflow_id)
        if proposal is None:
            return self.base_capability
        # Inherit ceiling of the workflow's declared trust (capped at L2).
        return max(self.base_capability, proposal.trust_level)

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, WorkflowInvokeInput)
        store = get_workflow_store()
        try:
            proposal = require_accepted(store, params.workflow_id)
            steps = expand_workflow(store, params.workflow_id, params.parameters)
        except WorkflowNotFoundError as exc:
            raise ToolError(str(exc), code=ErrorCode.NOT_FOUND) from exc
        except WorkflowError as exc:
            raise ToolError(str(exc), code=exc.code) from exc
        return WorkflowInvokeOutput(
            workflow_id=proposal.id,
            name=proposal.name,
            trust_level=proposal.trust_level,
            steps=steps,
        )
