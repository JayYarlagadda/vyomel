"""Tool contract.

The planner, policy engine, runtime, and verifier all consume this metadata.
An incomplete declaration is a correctness bug — ``tests/tools/test_contract.py``
walks the registry and asserts every rule in docs/05-TOOL-SPEC.md section 1.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from vyomel.core.cancel import CancellationToken
from vyomel.core.clock import Clock
from vyomel.core.config import Settings
from vyomel.core.types import Capability


@dataclass
class PreflightResult:
    ok: bool
    reason: str | None = None


@dataclass
class ToolContext:
    task_id: str
    action_id: str
    capability_granted: Capability
    scratch_dir: Path
    allowed_roots: list[Path]
    deadline: datetime
    cancel: CancellationToken
    clock: Clock
    settings: Settings | None = None
    # fs.delete moves here rather than unlinking, so a cancel can restore.
    trash_dir: Path | None = None


class Tool(ABC):
    name: ClassVar[str]
    version: ClassVar[str]
    description: ClassVar[str]
    Input: ClassVar[type[BaseModel]]
    Output: ClassVar[type[BaseModel]]
    base_capability: ClassVar[Capability]
    reversible: ClassVar[bool] = False
    idempotent: ClassVar[bool] = True
    actuation_tier: ClassVar[int] = 1
    host_bound: ClassVar[bool] = False
    concurrency_key: ClassVar[str | None] = None
    default_timeout_s: ClassVar[int] = 30

    def classify(self, params: BaseModel) -> Capability:
        """May raise the level based on parameters, never lower it."""
        return self.base_capability

    async def preflight(self, params: BaseModel, ctx: ToolContext) -> PreflightResult:
        if ctx.cancel.cancelled:
            return PreflightResult(ok=False, reason="cancelled")
        if ctx.clock.now() >= ctx.deadline:
            return PreflightResult(ok=False, reason="deadline")
        return PreflightResult(ok=True)

    @abstractmethod
    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel: ...

    async def compensate(self, params: BaseModel, result: BaseModel, ctx: ToolContext) -> None:
        if self.reversible:
            raise NotImplementedError(f"{self.name} is reversible but has no compensate()")

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        return []


@dataclass
class ToolSpec:
    """Frozen view of a tool's contract, used by the planner and tests."""

    name: str
    version: str
    description: str
    base_capability: Capability
    reversible: bool
    idempotent: bool
    actuation_tier: int
    concurrency_key: str | None
    input_schema: dict[str, Any] = field(default_factory=dict)
