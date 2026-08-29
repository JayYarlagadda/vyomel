"""shell.run allowlist (FR-601, FR-608)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from astra.core.cancel import CancellationToken
from astra.core.clock import SystemClock
from astra.core.errors import ErrorCode, ToolError
from astra.core.types import Capability
from astra.tools.base import ToolContext
from astra.tools.shell import ShellRun, ShellRunInput, allowed_programs


def _ctx(root: Path) -> ToolContext:
    scratch = root / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        task_id="t",
        action_id="a" * 26,
        capability_granted=Capability.L0,
        scratch_dir=scratch,
        allowed_roots=[root],
        deadline=datetime.now(UTC) + timedelta(seconds=30),
        cancel=CancellationToken(),
        clock=SystemClock(),
        trash_dir=root / "trash",
    )


@pytest.mark.req("FR-601")
def test_allowlist_is_read_only() -> None:
    allowed = allowed_programs()
    assert "git" in allowed
    assert "hostname" in allowed
    assert "whoami" in allowed
    assert "rm" not in allowed
    assert "cmd" not in allowed


@pytest.mark.req("FR-608")
async def test_mutating_git_subcommand_is_denied(tmp_path: Path) -> None:
    tool = ShellRun()
    with pytest.raises(ToolError) as caught:
        await tool.execute(
            ShellRunInput(argv=["git", "commit", "-m", "nope"]),
            _ctx(tmp_path),
        )
    assert caught.value.code is ErrorCode.PERMISSION_DENIED


@pytest.mark.req("FR-608")
async def test_a_path_as_argv0_cannot_escape_the_allowlist(tmp_path: Path) -> None:
    tool = ShellRun()
    with pytest.raises(ToolError) as caught:
        await tool.execute(
            ShellRunInput(argv=[r"C:\Windows\System32\cmd.exe", "/c", "echo hi"]),
            _ctx(tmp_path),
        )
    assert caught.value.code is ErrorCode.PERMISSION_DENIED


@pytest.mark.req("FR-601")
async def test_whoami_runs(tmp_path: Path) -> None:
    tool = ShellRun()
    result = await tool.execute(ShellRunInput(argv=["whoami"]), _ctx(tmp_path))
    assert result.exit_code == 0
    assert result.stdout.strip()
