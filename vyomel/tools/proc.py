"""Allowlisted subprocess.

Tools that shell out go through here so there is one place that forbids
``shell=True`` and one place that resolves the executable. The caller names a
program; we look it up on PATH and refuse a user-supplied path, so
``C:\\Windows\\System32\\cmd.exe`` cannot ride in as ``argv[0]``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from vyomel.core.errors import ErrorCode, ToolError

_MAX_OUTPUT_BYTES = 1_000_000


def resolve_program(name: str) -> Path:
    """Resolve an allowlisted program name to an absolute path.

    Only the basename is considered. A path the caller supplied is ignored.
    """
    basename = Path(name).name
    if not basename or basename in {".", ".."}:
        raise ToolError("Invalid program name", code=ErrorCode.INVALID_PARAMETERS)
    found = shutil.which(basename)
    if found is None:
        raise ToolError(
            f"{basename} is not on PATH",
            code=ErrorCode.PRECONDITION_FAILED,
            observation=basename,
        )
    return Path(found)


def run_argv(
    argv: list[str],
    *,
    cwd: Path,
    timeout_s: int,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run ``argv`` with ``argv[0]`` already an absolute path we resolved."""
    if not argv:
        raise ToolError("Empty argv", code=ErrorCode.INVALID_PARAMETERS)
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    if extra_env:
        env.update(extra_env)
    try:
        completed = subprocess.run(  # noqa: S603 — argv[0] is resolved from an allowlist
            argv,
            cwd=cwd,
            capture_output=True,
            timeout=timeout_s,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(
            f"Command timed out after {timeout_s}s",
            code=ErrorCode.TIMEOUT,
            observation=" ".join(argv[:4]),
        ) from exc
    except OSError as exc:
        raise ToolError(
            "Command could not be started",
            code=ErrorCode.TRANSIENT_IO,
            observation=str(exc),
        ) from exc
    return completed


def decode_capped(data: bytes, *, limit: int = _MAX_OUTPUT_BYTES) -> str:
    text = data[:limit].decode("utf-8", errors="replace")
    if len(data) > limit:
        text += "\n…(truncated)"
    return text
