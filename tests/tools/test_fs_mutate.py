"""fs.move / fs.copy / fs.delete: classify, mutate, verify, compensate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vyomel.core.cancel import CancellationToken
from vyomel.core.clock import SystemClock
from vyomel.core.errors import ErrorCode, ToolError
from vyomel.core.ids import digest_bytes, file_digest
from vyomel.core.types import Capability, VerifyOutcome
from vyomel.tools.base import ToolContext
from vyomel.tools.fs import (
    Copy,
    CopyInput,
    Delete,
    DeleteInput,
    Move,
    MoveInput,
)
from vyomel.verify.engine import verify_result


def _ctx(root: Path, *, action_id: str = "a" * 26) -> ToolContext:
    scratch = root / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    trash = root / "trash"
    trash.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        task_id="t",
        action_id=action_id,
        capability_granted=Capability.L2,
        scratch_dir=scratch,
        allowed_roots=[root],
        deadline=datetime.now(UTC) + timedelta(seconds=30),
        cancel=CancellationToken(),
        clock=SystemClock(),
        trash_dir=trash,
    )


def _assert_text_file(path: Path, content: str) -> None:
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == content


def _assert_dir(path: Path) -> None:
    assert path.is_dir()


def _passes(tool: object, params: object, result: object, root: Path) -> None:
    report = verify_result(
        capability=Capability.L2,
        postconditions=tool.verification_plan(params, result),  # type: ignore[attr-defined]
        result=result.model_dump(mode="json"),  # type: ignore[attr-defined]
        allowed_roots=[root],
    )
    assert report.outcome is VerifyOutcome.PASS


@pytest.mark.req("FR-601")
async def test_move_then_compensate_restores_both_sides(tmp_path: Path) -> None:
    src = tmp_path / "a.txt"
    dest = tmp_path / "b.txt"
    src.write_text("hello", encoding="utf-8")
    dest.write_text("old", encoding="utf-8")
    tool = Move()
    ctx = _ctx(tmp_path)
    params = MoveInput(src=str(src), dest=str(dest))
    result = await tool.execute(params, ctx)
    assert dest.read_text(encoding="utf-8") == "hello"
    assert not src.exists()
    _passes(tool, params, result, tmp_path)

    await tool.compensate(params, result, ctx)
    assert src.read_text(encoding="utf-8") == "hello"
    assert dest.read_text(encoding="utf-8") == "old"


@pytest.mark.req("FR-601")
async def test_copy_created_file_is_removed_on_compensate(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    dest = tmp_path / "dest.txt"
    src.write_text("payload", encoding="utf-8")
    tool = Copy()
    ctx = _ctx(tmp_path)
    params = CopyInput(src=str(src), dest=str(dest))
    result = await tool.execute(params, ctx)
    assert dest.read_text(encoding="utf-8") == "payload"
    assert result.sha256 == digest_bytes(b"payload")
    _passes(tool, params, result, tmp_path)

    await tool.compensate(params, result, ctx)
    assert src.exists()
    assert not dest.exists()


@pytest.mark.req("FR-601")
@pytest.mark.req("FR-401")
async def test_delete_moves_to_trash_and_restore_puts_it_back(tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("keep", encoding="utf-8")
    tool = Delete()
    ctx = _ctx(tmp_path)
    params = DeleteInput(path=str(target))
    result = await tool.execute(params, ctx)
    assert not target.exists()
    _assert_text_file(Path(result.trashed_to), "keep")
    assert result.sha256 == file_digest(Path(result.trashed_to))
    _passes(tool, params, result, tmp_path)

    await tool.compensate(params, result, ctx)
    assert target.read_text(encoding="utf-8") == "keep"


@pytest.mark.req("FR-601")
@pytest.mark.req("FR-301")
def test_delete_of_a_directory_tree_is_l4(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.txt").write_text("x", encoding="utf-8")
    tool = Delete()
    assert tool.classify(DeleteInput(path=str(tree))) is Capability.L4
    assert tool.classify(DeleteInput(path=str(tmp_path / "missing.txt"))) is Capability.L2


@pytest.mark.req("FR-601")
async def test_delete_of_a_directory_is_restored_from_trash(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.txt").write_text("x", encoding="utf-8")
    tool = Delete()
    ctx = _ctx(tmp_path, action_id="b" * 26)
    ctx.capability_granted = Capability.L4
    params = DeleteInput(path=str(tree))
    result = await tool.execute(params, ctx)
    assert not tree.exists()
    _assert_dir(Path(result.trashed_to))

    await tool.compensate(params, result, ctx)
    assert (tree / "a.txt").read_text(encoding="utf-8") == "x"


@pytest.mark.req("FR-608")
async def test_delete_never_unlinks_a_missing_path_without_trash(tmp_path: Path) -> None:
    tool = Delete()
    with pytest.raises(ToolError) as caught:
        await tool.execute(DeleteInput(path=str(tmp_path / "nope.txt")), _ctx(tmp_path))
    assert caught.value.code is ErrorCode.PRECONDITION_FAILED
