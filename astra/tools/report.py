"""``task.report`` — structured summary produced by a plan, not a state mutation.

The tool returns a payload. The scheduler copies it onto ``tasks.result`` when
the DAG completes. Tools must not write task rows (docs/05-TOOL-SPEC.md).
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field

from astra.core.types import Capability
from astra.tools.base import Tool, ToolContext


class ReportInput(BaseModel):
    summary: str = Field(min_length=1, max_length=8_000)
    findings: list[str] = Field(default_factory=list)


class ReportOutput(BaseModel):
    summary: str
    findings: list[str]


class TaskReport(Tool):
    name: ClassVar[str] = "task.report"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Record a structured summary of work done in this task. Does not change "
        "any files or external systems. Use as a terminal action in a handwritten plan."
    )
    Input: ClassVar[type[BaseModel]] = ReportInput
    Output: ClassVar[type[BaseModel]] = ReportOutput
    base_capability: ClassVar[Capability] = Capability.L0
    reversible: ClassVar[bool] = False
    idempotent: ClassVar[bool] = True
    default_timeout_s: ClassVar[int] = 5

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, ReportInput)
        return ReportOutput(summary=params.summary, findings=list(params.findings))
