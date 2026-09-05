"""Resolve ingest paths against the filesystem allowlist.

Duplicated from the tool sandbox on purpose: ``memory`` cannot import ``tools``,
and the check has to live on this side of the layering line (FR-603).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from vyomel.core.errors import ErrorCode, PermissionDeniedError, VyomelError

INGESTIBLE_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".html", ".pdf", ".docx"})


def resolve_allowed(path: str, allowed_roots: Sequence[Path]) -> Path:
    if not allowed_roots:
        raise PermissionDeniedError("No filesystem roots are allowlisted")
    if not path or "\x00" in path:
        raise VyomelError("Invalid path", code=ErrorCode.INVALID_PARAMETERS)

    try:
        resolved = Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PermissionDeniedError("Path could not be resolved") from exc

    for root in allowed_roots:
        try:
            root_resolved = root.expanduser().resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if resolved == root_resolved or resolved.is_relative_to(root_resolved):
            return resolved

    raise PermissionDeniedError(
        "Path is outside the allowlisted roots",
        detail={"path": str(resolved)},
    )


def is_ingestible(path: Path) -> bool:
    return path.suffix.lower() in INGESTIBLE_SUFFIXES


def mime_for(path: Path) -> str:
    suffix = path.suffix.lower()
    mapping = {
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".txt": "text/plain",
        ".html": "text/html",
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    mime = mapping.get(suffix)
    if mime is None:
        raise VyomelError(
            f"Unsupported document type: {suffix}",
            code=ErrorCode.INVALID_PARAMETERS,
            detail={"path": str(path)},
        )
    return mime
