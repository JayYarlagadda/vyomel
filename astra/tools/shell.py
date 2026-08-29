"""``shell.run``: read-only allowlisted commands.

The catalog in docs/05 §3.1 is L0 and not reversible. Mutation belongs to
dedicated tools (``git.commit``, ``fs.*``); a command that is not on the
allowlist is ``PERMISSION_DENIED``, not a surprising side effect.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from astra.core.errors import ErrorCode, ToolError
from astra.core.types import Capability
from astra.tools.base import Tool, ToolContext
from astra.tools.proc import decode_capped, resolve_program, run_argv
from astra.tools.sandbox import resolve_in_sandbox

# Basename → allowed first argument after the program, or None when any
# remaining args are accepted because the program itself is read-only.
# git's mutating subcommands are deliberately absent: those are git.* tools.
_ALLOWED: dict[str, frozenset[str] | None] = {
    "git": frozenset(
        {
            "status",
            "diff",
            "log",
            "show",
            "rev-parse",
            "ls-files",
            "describe",
            "version",
            "--version",
        }
    ),
    "hostname": None,
    "whoami": None,
}

_ALLOWED_BASES = frozenset(_ALLOWED) | {f"{name}.exe" for name in _ALLOWED}


class ShellRunInput(BaseModel):
    argv: list[str] = Field(min_length=1, max_length=32)
    cwd: str | None = None
    timeout_s: int = Field(default=30, ge=1, le=120)


class ShellRunOutput(BaseModel):
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str


class ShellRun(Tool):
    name: ClassVar[str] = "shell.run"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Run a read-only allowlisted command (git status/diff/log, hostname, "
        "whoami). Mutating commands are refused; use the dedicated git and fs "
        "tools. argv[0] is looked up on PATH; a caller-supplied path is ignored."
    )
    Input: ClassVar[type[BaseModel]] = ShellRunInput
    Output: ClassVar[type[BaseModel]] = ShellRunOutput
    base_capability: ClassVar[Capability] = Capability.L0
    reversible: ClassVar[bool] = False
    idempotent: ClassVar[bool] = False
    concurrency_key: ClassVar[str] = "shell"
    default_timeout_s: ClassVar[int] = 30

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, ShellRunInput)
        binary, rest = _allow(params.argv)
        cwd = _cwd(params.cwd, ctx)
        completed = run_argv(
            [str(binary), *rest],
            cwd=cwd,
            timeout_s=min(params.timeout_s, self.default_timeout_s),
        )
        return ShellRunOutput(
            argv=[binary.name, *rest],
            exit_code=completed.returncode,
            stdout=decode_capped(completed.stdout),
            stderr=decode_capped(completed.stderr),
        )


def allowed_programs() -> frozenset[str]:
    return frozenset(_ALLOWED)


def _allow(argv: list[str]) -> tuple[Path, list[str]]:
    requested = Path(argv[0]).name.lower()
    key = requested.removesuffix(".exe")
    if requested not in _ALLOWED_BASES and key not in _ALLOWED:
        raise ToolError(
            f"{requested} is not on the shell.run allowlist",
            code=ErrorCode.PERMISSION_DENIED,
            observation=requested,
        )
    spec = _ALLOWED[key]
    rest = argv[1:]
    if spec is not None:
        if not rest:
            raise ToolError(
                f"{key} requires a subcommand",
                code=ErrorCode.INVALID_PARAMETERS,
            )
        sub = rest[0]
        if sub.startswith("-") and sub not in spec:
            # Flags before the subcommand (git --version) are listed explicitly.
            raise ToolError(
                f"{key} {sub} is not a read-only allowlisted subcommand",
                code=ErrorCode.PERMISSION_DENIED,
                observation=sub,
            )
        if not sub.startswith("-") and sub not in spec:
            raise ToolError(
                f"{key} {sub} is not a read-only allowlisted subcommand",
                code=ErrorCode.PERMISSION_DENIED,
                observation=sub,
            )
    return resolve_program(key), rest


def _cwd(raw: str | None, ctx: ToolContext) -> Path:
    if raw is None:
        return ctx.scratch_dir
    return resolve_in_sandbox(raw, ctx.allowed_roots)
