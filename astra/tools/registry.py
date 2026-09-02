"""Tool registry.

A tool that fails the contract cannot be registered. The catalog the planner
(M5) will see is exactly this registry, so a missing field here is a missing
field in every model prompt later.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from astra.core.errors import AstraError, ErrorCode
from astra.core.types import Capability
from astra.tools.base import Tool, ToolSpec

_NAME = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


class RegistryError(AstraError):
    code = ErrorCode.INTERNAL


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        _assert_contract(tool)
        if tool.name in self._tools:
            raise RegistryError(f"Duplicate tool registration: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None:
            raise RegistryError(f"Unknown tool: {name}", code=ErrorCode.NOT_FOUND)
        return tool

    def names(self) -> list[str]:
        return sorted(self._tools)

    def catalog(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name=tool.name,
                version=tool.version,
                description=tool.description,
                base_capability=tool.base_capability,
                reversible=tool.reversible,
                idempotent=tool.idempotent,
                actuation_tier=tool.actuation_tier,
                concurrency_key=tool.concurrency_key,
                input_schema=tool.Input.model_json_schema(),
            )
            for tool in self._tools.values()
        ]

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)


def default_registry() -> ToolRegistry:
    from astra.tools.fs import Copy, Delete, ListDir, Move, ReadFile, WriteFile
    from astra.tools.git import GitCommit, GitDiff, GitPush, GitStatus
    from astra.tools.memory import MemoryForget, MemoryGetEntity, MemoryQuery, MemoryRemember
    from astra.tools.report import TaskReport
    from astra.tools.shell import ShellRun

    registry = ToolRegistry()
    registry.register(ReadFile())
    registry.register(ListDir())
    registry.register(WriteFile())
    registry.register(Move())
    registry.register(Copy())
    registry.register(Delete())
    registry.register(ShellRun())
    registry.register(GitStatus())
    registry.register(GitDiff())
    registry.register(GitCommit())
    registry.register(GitPush())
    registry.register(MemoryQuery())
    registry.register(MemoryGetEntity())
    registry.register(MemoryRemember())
    registry.register(MemoryForget())
    registry.register(TaskReport())
    return registry


def _assert_contract(tool: Tool) -> None:
    if not _NAME.match(tool.name):
        raise RegistryError(f"Tool name {tool.name!r} is not dotted_group.action")
    if not _SEMVER.match(tool.version):
        raise RegistryError(f"Tool {tool.name} version {tool.version!r} is not semver")
    if not tool.description.strip():
        raise RegistryError(f"Tool {tool.name} has an empty description")
    if not issubclass(tool.Input, BaseModel) or not issubclass(tool.Output, BaseModel):
        raise RegistryError(f"Tool {tool.name} Input/Output must be Pydantic models")
    if tool.base_capability >= Capability.L2 and (
        type(tool).verification_plan is Tool.verification_plan
    ):
        raise RegistryError(f"Tool {tool.name} is >= L2 and must declare a verification_plan")
    if tool.reversible and type(tool).compensate is Tool.compensate:
        raise RegistryError(f"Tool {tool.name} is reversible and must implement compensate()")
    if not 1 <= tool.actuation_tier <= 4:
        raise RegistryError(f"Tool {tool.name} actuation_tier must be 1-4")
