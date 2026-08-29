"""Git tools.

``git.status`` / ``git.diff`` are L0 observations. ``git.commit`` is L2 and
reversible via ``reset --soft`` of the commit this tool created. ``git.push``
is L3 and irreversible; the worker's side-effect ledger covers replay.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from astra.core.errors import ErrorCode, ToolError
from astra.core.types import Capability
from astra.tools.base import Tool, ToolContext
from astra.tools.proc import decode_capped, resolve_program, run_argv
from astra.tools.sandbox import resolve_in_sandbox

_GIT_TIMEOUT_S = 30
_PUSH_TIMEOUT_S = 60


class GitRepoInput(BaseModel):
    repo: str


class GitStatusOutput(BaseModel):
    repo: str
    porcelain: str
    branch: str


class GitDiffOutput(BaseModel):
    repo: str
    diff: str


class GitCommitInput(BaseModel):
    repo: str
    message: str = Field(min_length=1, max_length=4_000)
    paths: list[str] = Field(default_factory=list, max_length=100)


class GitCommitOutput(BaseModel):
    repo: str
    sha: str
    parent: str | None
    message: str


class GitPushInput(BaseModel):
    repo: str
    remote: str = "origin"
    branch: str = "HEAD"


class GitPushOutput(BaseModel):
    repo: str
    remote: str
    branch: str
    ok: bool
    summary: str


class GitStatus(Tool):
    name: ClassVar[str] = "git.status"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Show the porcelain status and current branch of a git repository "
        "inside the allowlisted roots. Does not mutate the repo."
    )
    Input: ClassVar[type[BaseModel]] = GitRepoInput
    Output: ClassVar[type[BaseModel]] = GitStatusOutput
    base_capability: ClassVar[Capability] = Capability.L0
    reversible: ClassVar[bool] = True
    idempotent: ClassVar[bool] = True
    concurrency_key: ClassVar[str] = "git"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, GitRepoInput)
        repo = _repo(params.repo, ctx)
        porcelain = _git(repo, ["status", "--porcelain=v1"])
        branch = _branch(repo)
        return GitStatusOutput(repo=str(repo), porcelain=porcelain, branch=branch)

    async def compensate(self, params: BaseModel, result: BaseModel, ctx: ToolContext) -> None:
        del params, result, ctx


class GitDiff(Tool):
    name: ClassVar[str] = "git.diff"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Show the combined unstaged and staged diff of a git repository inside "
        "the allowlisted roots. Does not mutate the repo."
    )
    Input: ClassVar[type[BaseModel]] = GitRepoInput
    Output: ClassVar[type[BaseModel]] = GitDiffOutput
    base_capability: ClassVar[Capability] = Capability.L0
    reversible: ClassVar[bool] = True
    idempotent: ClassVar[bool] = True
    concurrency_key: ClassVar[str] = "git"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, GitRepoInput)
        repo = _repo(params.repo, ctx)
        unstaged = _git(repo, ["diff"])
        staged = _git(repo, ["diff", "--cached"])
        parts = [p for p in (unstaged, staged) if p]
        return GitDiffOutput(repo=str(repo), diff="".join(parts))

    async def compensate(self, params: BaseModel, result: BaseModel, ctx: ToolContext) -> None:
        del params, result, ctx


class GitCommit(Tool):
    """Stage optional paths and create a commit. Undo is ``reset --soft``."""

    name: ClassVar[str] = "git.commit"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Stage the given paths (or the already-staged index) and create a "
        "commit. Reversible by resetting the commit this tool created, and "
        "only that commit: a later commit on the same branch is left alone."
    )
    Input: ClassVar[type[BaseModel]] = GitCommitInput
    Output: ClassVar[type[BaseModel]] = GitCommitOutput
    base_capability: ClassVar[Capability] = Capability.L2
    reversible: ClassVar[bool] = True
    idempotent: ClassVar[bool] = True
    concurrency_key: ClassVar[str] = "git"

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        assert isinstance(result, GitCommitOutput)
        return [
            {
                "type": "value_equals",
                "field": "sha",
                "expected": result.sha,
                "tier": 1,
            },
            {
                "type": "file_exists",
                "path": str(Path(result.repo) / ".git" / "HEAD"),
                "tier": 1,
            },
        ]

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, GitCommitInput)
        repo = _repo(params.repo, ctx)
        parent = _rev_or_none(repo)
        for raw in params.paths:
            target = resolve_in_sandbox(raw, ctx.allowed_roots)
            _git(repo, ["add", "--", str(target)])
        _git(repo, ["commit", "--allow-empty", "--no-verify", "-m", params.message])
        sha = _git(repo, ["rev-parse", "HEAD"]).strip()
        return GitCommitOutput(repo=str(repo), sha=sha, parent=parent, message=params.message)

    async def compensate(self, params: BaseModel, result: BaseModel, ctx: ToolContext) -> None:
        assert isinstance(params, GitCommitInput)
        assert isinstance(result, GitCommitOutput)
        repo = _repo(params.repo, ctx)
        head = _rev_or_none(repo)
        if head != result.sha:
            return
        if result.parent is None:
            _git(repo, ["update-ref", "-d", "HEAD"])
            return
        _git(repo, ["reset", "--soft", result.parent])


class GitPush(Tool):
    name: ClassVar[str] = "git.push"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Push a branch to a remote. Externally visible and not reversible. "
        "Requires confirmation at L3."
    )
    Input: ClassVar[type[BaseModel]] = GitPushInput
    Output: ClassVar[type[BaseModel]] = GitPushOutput
    base_capability: ClassVar[Capability] = Capability.L3
    reversible: ClassVar[bool] = False
    idempotent: ClassVar[bool] = False
    concurrency_key: ClassVar[str] = "git"
    default_timeout_s: ClassVar[int] = _PUSH_TIMEOUT_S

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        assert isinstance(result, GitPushOutput)
        return [
            {"type": "value_equals", "field": "ok", "expected": True, "tier": 1},
            {
                "type": "value_equals",
                "field": "remote",
                "expected": result.remote,
                "tier": 1,
            },
        ]

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, GitPushInput)
        repo = _repo(params.repo, ctx)
        _assert_ref(params.remote, "remote")
        _assert_ref(params.branch, "branch")
        binary = resolve_program("git")
        pushed = run_argv(
            [str(binary), "push", "--", params.remote, params.branch],
            cwd=repo,
            timeout_s=_PUSH_TIMEOUT_S,
        )
        if pushed.returncode != 0:
            raise ToolError(
                "git push failed",
                code=ErrorCode.PRECONDITION_FAILED,
                observation=decode_capped(pushed.stderr)[:400],
            )
        return GitPushOutput(
            repo=str(repo),
            remote=params.remote,
            branch=params.branch,
            ok=True,
            summary=decode_capped(pushed.stderr or pushed.stdout),
        )


def _assert_ref(value: str, label: str) -> None:
    if not value or value.startswith("-") or "\\" in value or "\x00" in value:
        raise ToolError(
            f"{label} name is not a simple ref",
            code=ErrorCode.INVALID_PARAMETERS,
            observation=value,
        )


def _repo(raw: str, ctx: ToolContext) -> Path:
    repo = resolve_in_sandbox(raw, ctx.allowed_roots)
    git_dir = repo / ".git"
    bare = repo.suffix == ".git" and (repo / "HEAD").exists()
    if not git_dir.exists() and not bare:
        raise ToolError(
            "Path is not a git repository",
            code=ErrorCode.PRECONDITION_FAILED,
            observation=str(repo),
        )
    return repo


def _git(repo: Path, args: list[str], *, timeout_s: int = _GIT_TIMEOUT_S) -> str:
    binary = resolve_program("git")
    completed = run_argv([str(binary), *args], cwd=repo, timeout_s=timeout_s)
    if completed.returncode != 0:
        raise ToolError(
            f"git {' '.join(args[:3])} failed",
            code=ErrorCode.PRECONDITION_FAILED,
            observation=decode_capped(completed.stderr)[:400],
        )
    return decode_capped(completed.stdout)


def _branch(repo: Path) -> str:
    """Current branch name, including unborn branches (no commits yet)."""
    binary = resolve_program("git")
    completed = run_argv(
        [str(binary), "symbolic-ref", "--short", "HEAD"],
        cwd=repo,
        timeout_s=_GIT_TIMEOUT_S,
    )
    if completed.returncode == 0:
        name = decode_capped(completed.stdout).strip()
        if name:
            return name
    completed = run_argv(
        [str(binary), "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo,
        timeout_s=_GIT_TIMEOUT_S,
    )
    if completed.returncode == 0:
        name = decode_capped(completed.stdout).strip()
        if name:
            return name
    return "HEAD"


def _rev_or_none(repo: Path) -> str | None:
    binary = resolve_program("git")
    completed = run_argv(
        [str(binary), "rev-parse", "--verify", "HEAD"],
        cwd=repo,
        timeout_s=_GIT_TIMEOUT_S,
    )
    if completed.returncode != 0:
        return None
    sha = decode_capped(completed.stdout).strip()
    return sha or None
