"""Tool contract (FR-601, FR-602) and sandbox (FR-603)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from astra.core.cancel import CancellationToken
from astra.core.clock import SystemClock
from astra.core.errors import ErrorCode, ToolError
from astra.core.types import Capability
from astra.tools.base import Tool, ToolContext
from astra.tools.registry import RegistryError, ToolRegistry, default_registry
from astra.tools.sandbox import resolve_in_sandbox


def _ctx(root: Path) -> ToolContext:
    from datetime import UTC, datetime, timedelta

    return ToolContext(
        task_id="t",
        action_id="a",
        capability_granted=Capability.L0,
        scratch_dir=root / "scratch",
        allowed_roots=[root],
        deadline=datetime.now(UTC) + timedelta(seconds=30),
        cancel=CancellationToken(),
        clock=SystemClock(),
        trash_dir=root / "trash",
    )


@pytest.mark.req("FR-601")
@pytest.mark.req("FR-602")
def test_default_registry_meets_the_contract() -> None:
    registry = default_registry()
    assert set(registry.names()) == {
        "fs.copy",
        "fs.delete",
        "fs.list_dir",
        "fs.move",
        "fs.read_file",
        "fs.write_file",
        "git.commit",
        "git.diff",
        "git.push",
        "git.status",
        "memory.forget",
        "memory.get_entity",
        "memory.query",
        "memory.remember",
        "shell.run",
        "task.report",
    }
    by_name = {s.name: s for s in registry.catalog()}
    for spec in by_name.values():
        assert spec.input_schema
        assert spec.actuation_tier == 1
    assert by_name["fs.write_file"].base_capability is Capability.L1
    assert by_name["fs.write_file"].reversible is True
    assert by_name["fs.move"].base_capability is Capability.L2
    assert by_name["fs.copy"].reversible is True
    assert by_name["fs.delete"].reversible is True
    assert by_name["git.commit"].base_capability is Capability.L2
    assert by_name["git.commit"].reversible is True
    assert by_name["git.push"].base_capability is Capability.L3
    assert by_name["git.push"].reversible is False
    assert by_name["git.push"].idempotent is False
    assert by_name["shell.run"].idempotent is False
    assert by_name["shell.run"].reversible is False
    assert by_name["git.status"].reversible is True
    assert by_name["fs.read_file"].base_capability is Capability.L0


@pytest.mark.req("FR-601")
def test_l2_tool_without_verification_plan_is_rejected() -> None:
    class Bad(Tool):
        name = "test.bad"
        version = "1.0.0"
        description = "bad"
        Input = BaseModel
        Output = BaseModel
        base_capability = Capability.L2

        async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
            return params

    with pytest.raises(RegistryError):
        ToolRegistry().register(Bad())


@pytest.mark.req("FR-603")
def test_sandbox_rejects_traversal(tmp_path: Path) -> None:
    (tmp_path / "inside.txt").write_text("ok", encoding="utf-8")
    resolved = resolve_in_sandbox(str(tmp_path / "inside.txt"), [tmp_path])
    assert resolved == (tmp_path / "inside.txt").resolve()

    with pytest.raises(ToolError) as exc:
        resolve_in_sandbox(str(tmp_path / ".." / "outside.txt"), [tmp_path])
    assert exc.value.code is ErrorCode.PERMISSION_DENIED


@pytest.mark.req("FR-603")
def test_sandbox_fails_closed_on_empty_allowlist(tmp_path: Path) -> None:
    with pytest.raises(ToolError) as exc:
        resolve_in_sandbox(str(tmp_path), [])
    assert exc.value.code is ErrorCode.PERMISSION_DENIED


@pytest.mark.req("FR-603")
@pytest.mark.req("FR-608")
async def test_read_file_and_list_dir(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    registry = default_registry()
    ctx = _ctx(tmp_path)

    listed = await registry.get("fs.list_dir").execute(
        registry.get("fs.list_dir").Input(path=str(tmp_path)), ctx
    )
    names = {e.name for e in listed.entries}  # type: ignore[attr-defined]
    assert names == {"a.txt", "sub"}

    read = await registry.get("fs.read_file").execute(
        registry.get("fs.read_file").Input(path=str(tmp_path / "a.txt")), ctx
    )
    assert read.content == "hello"  # type: ignore[attr-defined]


@pytest.mark.req("FR-608")
async def test_missing_file_is_a_structured_precondition_failure(tmp_path: Path) -> None:
    registry = default_registry()
    with pytest.raises(ToolError) as exc:
        await registry.get("fs.read_file").execute(
            registry.get("fs.read_file").Input(path=str(tmp_path / "nope.txt")),
            _ctx(tmp_path),
        )
    assert exc.value.code is ErrorCode.PRECONDITION_FAILED
    assert exc.value.retryable is False
