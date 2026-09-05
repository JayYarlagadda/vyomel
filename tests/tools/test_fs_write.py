"""fs.write_file: classify, write, verify, compensate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vyomel.core.cancel import CancellationToken
from vyomel.core.clock import SystemClock
from vyomel.core.ids import digest_bytes
from vyomel.core.types import Capability, VerifyOutcome
from vyomel.tools.base import ToolContext
from vyomel.tools.fs import WriteFile, WriteFileInput, WriteFileOutput
from vyomel.tools.registry import default_registry
from vyomel.verify.engine import verify_result


def _ctx(root: Path, *, scratch: Path | None = None) -> ToolContext:
    scratch_dir = scratch or (root / "scratch")
    scratch_dir.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        task_id="t",
        action_id="a" * 26,
        capability_granted=Capability.L2,
        scratch_dir=scratch_dir,
        allowed_roots=[root],
        deadline=datetime.now(UTC) + timedelta(seconds=30),
        cancel=CancellationToken(),
        clock=SystemClock(),
        trash_dir=scratch_dir.parent / "trash",
    )


@pytest.mark.req("FR-601")
def test_write_file_is_l1_in_scratch_and_l2_elsewhere(tmp_path: Path) -> None:
    tool = WriteFile()
    scratch = tool.classify(WriteFileInput(path=str(tmp_path / "scratch" / "a.txt"), content="x"))
    elsewhere = tool.classify(WriteFileInput(path=str(tmp_path / "notes" / "a.txt"), content="x"))
    assert scratch is Capability.L1
    assert elsewhere is Capability.L2
    assert elsewhere > scratch


@pytest.mark.req("FR-601")
@pytest.mark.req("FR-401")
async def test_write_then_reobserve_passes(tmp_path: Path) -> None:
    tool = WriteFile()
    ctx = _ctx(tmp_path)
    target = tmp_path / "scratch" / "out.txt"
    params = WriteFileInput(path=str(target), content="87")
    result = await tool.execute(params, ctx)
    assert isinstance(result, WriteFileOutput)
    assert target.read_text(encoding="utf-8") == "87"
    assert result.sha256 == digest_bytes(b"87")
    assert result.created is True

    report = verify_result(
        capability=Capability.L1,
        postconditions=tool.verification_plan(params, result),
        result=result.model_dump(mode="json"),
        allowed_roots=[tmp_path],
    )
    assert report.outcome is VerifyOutcome.PASS
    assert {c.verifier for c in report.checks} == {"file_exists", "file_hash"}


@pytest.mark.req("FR-601")
async def test_overwrite_can_be_compensated(tmp_path: Path) -> None:
    original = tmp_path / "notes.txt"
    original.write_text("old", encoding="utf-8")
    tool = WriteFile()
    ctx = _ctx(tmp_path)
    params = WriteFileInput(path=str(original), content="new")
    result = await tool.execute(params, ctx)
    assert isinstance(result, WriteFileOutput)
    assert original.read_text(encoding="utf-8") == "new"
    assert result.created is False
    assert result.backup_path is not None

    await tool.compensate(params, result, ctx)
    assert original.read_text(encoding="utf-8") == "old"


@pytest.mark.req("FR-601")
async def test_created_file_is_removed_on_compensate(tmp_path: Path) -> None:
    tool = WriteFile()
    ctx = _ctx(tmp_path)
    target = tmp_path / "scratch" / "fresh.txt"
    params = WriteFileInput(path=str(target), content="hello")
    result = await tool.execute(params, ctx)
    assert target.exists()
    await tool.compensate(params, result, ctx)
    assert not target.exists()


@pytest.mark.req("FR-601")
def test_write_file_is_in_the_production_registry() -> None:
    registry = default_registry()
    assert "fs.write_file" in registry
    spec = next(s for s in registry.catalog() if s.name == "fs.write_file")
    assert spec.reversible is True
    assert spec.idempotent is True
    assert spec.base_capability is Capability.L1
