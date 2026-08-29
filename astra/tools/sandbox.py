r"""Filesystem sandbox.

FR-603: tools operate only inside configured allowlisted roots; traversal is
rejected. Fail closed if the allowlist is empty — an unconfigured agent that
can read ``C:\`` is worse than one that can read nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from astra.core.errors import ErrorCode, ToolError


def resolve_in_sandbox(path: str, allowed_roots: Sequence[Path]) -> Path:
    if not allowed_roots:
        raise ToolError(
            "No filesystem roots are allowlisted",
            code=ErrorCode.PERMISSION_DENIED,
        )
    if not path or "\x00" in path:
        raise ToolError("Invalid path", code=ErrorCode.INVALID_PARAMETERS)

    try:
        resolved = Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ToolError(
            "Path could not be resolved",
            code=ErrorCode.INVALID_PARAMETERS,
            detail={"path": path},
        ) from exc

    for root in allowed_roots:
        try:
            root_resolved = root.expanduser().resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if resolved == root_resolved or resolved.is_relative_to(root_resolved):
            return resolved

    raise ToolError(
        "Path is outside the allowlisted roots",
        code=ErrorCode.PERMISSION_DENIED,
        detail={"path": str(resolved)},
        observation=f"resolved to {resolved}",
    )
