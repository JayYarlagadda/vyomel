"""git.status / diff / commit / push against a real local repo."""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from astra.core.cancel import CancellationToken
from astra.core.clock import SystemClock
from astra.core.types import Capability
from astra.tools.base import ToolContext
from astra.tools.git import (
    GitCommit,
    GitCommitInput,
    GitDiff,
    GitPush,
    GitPushInput,
    GitRepoInput,
    GitStatus,
)

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is not on PATH")


def _ctx(root: Path) -> ToolContext:
    scratch = root / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        task_id="t",
        action_id="a" * 26,
        capability_granted=Capability.L2,
        scratch_dir=scratch,
        allowed_roots=[root],
        deadline=datetime.now(UTC) + timedelta(seconds=30),
        cancel=CancellationToken(),
        clock=SystemClock(),
        trash_dir=root / "trash",
    )


def _run(repo: Path, *args: str) -> None:
    assert GIT is not None
    subprocess.run(
        [GIT, *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _git_out(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    assert GIT is not None
    return subprocess.run(
        [GIT, *args],
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
    )


def _init_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    _run(repo, "init")
    _run(repo, "config", "user.email", "astra@test")
    _run(repo, "config", "user.name", "Astra")
    _run(repo, "config", "commit.gpgsign", "false")
    return repo


@pytest.mark.req("FR-601")
async def test_status_and_diff_observe_without_mutating(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("hello", encoding="utf-8")
    ctx = _ctx(tmp_path)

    status = await GitStatus().execute(GitRepoInput(repo=str(repo)), ctx)
    assert "a.txt" in status.porcelain

    diff = await GitDiff().execute(GitRepoInput(repo=str(repo)), ctx)
    assert "hello" in diff.diff or diff.diff == ""  # untracked files have no diff
    assert (repo / "a.txt").read_text(encoding="utf-8") == "hello"


@pytest.mark.req("FR-601")
async def test_commit_is_reversed_by_soft_reset(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    target = repo / "a.txt"
    target.write_text("hello", encoding="utf-8")
    ctx = _ctx(tmp_path)
    tool = GitCommit()
    params = GitCommitInput(repo=str(repo), message="add a", paths=[str(target)])
    result = await tool.execute(params, ctx)
    assert result.sha
    head = _git_out(repo, "rev-parse", "HEAD").stdout.strip()
    assert head == result.sha

    await tool.compensate(params, result, ctx)
    after = _git_out(repo, "rev-parse", "--verify", "HEAD", check=False)
    assert after.returncode != 0 or after.stdout.strip() != result.sha
    assert target.read_text(encoding="utf-8") == "hello"


@pytest.mark.req("FR-601")
async def test_push_to_a_local_bare_remote(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    target = repo / "a.txt"
    target.write_text("hello", encoding="utf-8")
    ctx = _ctx(tmp_path)
    commit = await GitCommit().execute(
        GitCommitInput(repo=str(repo), message="add a", paths=[str(target)]),
        ctx,
    )
    bare = tmp_path / "remote.git"
    _run(tmp_path, "init", "--bare", str(bare))
    _run(repo, "remote", "add", "origin", str(bare))

    ctx.capability_granted = Capability.L3
    pushed = await GitPush().execute(
        GitPushInput(repo=str(repo), remote="origin", branch="HEAD"),
        ctx,
    )
    assert pushed.ok is True
    assert commit.sha
