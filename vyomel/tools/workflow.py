"""``workflow.invoke`` — run an accepted learned/saved workflow (docs/05 §3.7)."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from vyomel.core.errors import ErrorCode, ToolError
from vyomel.core.types import Capability
from vyomel.learning.pg_store import PostgresWorkflowStore
from vyomel.learning.proposal import bind_parameters
from vyomel.learning.store import (
    WorkflowError,
    WorkflowNotFoundError,
    expand_workflow,
    get_workflow_store,
    require_accepted,
)
from vyomel.store.db import session_scope
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
        # FR-310: learned workflows never exceed L2. Execute loads the exact
        # trust_level; classify uses the ceiling so we never under-escalate.
        return Capability.L2

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, WorkflowInvokeInput)
        settings = ctx.settings
        use_pg = settings is not None and settings.workflow_store_backend == "postgres"
        try:
            if use_pg:
                async with session_scope() as session:
                    store = PostgresWorkflowStore(session)
                    proposal = await store.get(params.workflow_id)
                    if proposal is None:
                        raise WorkflowNotFoundError(
                            f"unknown workflow: {params.workflow_id}"
                        )
                    if proposal.status != "accepted":
                        raise WorkflowError(
                            "workflow is not accepted and cannot be invoked",
                            detail={
                                "status": proposal.status,
                                "workflow_id": params.workflow_id,
                            },
                            code=ErrorCode.PERMISSION_DENIED,
                        )
                    steps = bind_parameters(proposal, params.parameters)
            else:
                mem = get_workflow_store()
                proposal = require_accepted(mem, params.workflow_id)
                steps = expand_workflow(mem, params.workflow_id, params.parameters)
        except WorkflowNotFoundError as exc:
            raise ToolError(str(exc), code=ErrorCode.NOT_FOUND) from exc
        except WorkflowError as exc:
            raise ToolError(str(exc), code=exc.code) from exc
        except ValueError as exc:
            raise ToolError(str(exc), code=ErrorCode.INVALID_PARAMETERS) from exc
        return WorkflowInvokeOutput(
            workflow_id=proposal.id,
            name=proposal.name,
            trust_level=proposal.trust_level,
            steps=steps,
        )
