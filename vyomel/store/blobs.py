"""Content-addressed blob store for large action results (docs/07 §9)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vyomel.core.errors import VyomelError, ErrorCode
from vyomel.core.ids import canonical_json, digest_bytes, file_digest

BLOB_REF_KEY = "$blob"


class BlobError(VyomelError):
    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message, code=ErrorCode.INTERNAL, detail=detail)


def is_blob_ref(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {BLOB_REF_KEY}
        and isinstance(value[BLOB_REF_KEY], str)
    )


def spill_if_large(result: dict[str, Any], *, blob_dir: Path, threshold: int) -> dict[str, Any]:
    """Persist ``result`` to the blob store when its JSON encoding exceeds ``threshold``."""
    if threshold <= 0:
        return result
    payload = canonical_json(result).encode("utf-8")
    if len(payload) <= threshold:
        return result
    digest = write_blob(payload, blob_dir)
    return {BLOB_REF_KEY: digest}


def resolve_result(result: dict[str, Any] | None, *, blob_dir: Path) -> dict[str, Any] | None:
    """Load a spilled result from the blob store."""
    if result is None or not is_blob_ref(result):
        return result
    return read_blob(result[BLOB_REF_KEY], blob_dir)


def write_blob(data: bytes, blob_dir: Path) -> str:
    """Write bytes content-addressed. Idempotent when the same digest already exists."""
    digest = digest_bytes(data)
    path = blob_path(blob_dir, digest)
    if path.exists():
        if file_digest(path) != digest:
            raise BlobError(
                "blob path collision",
                detail={"path": str(path), "expected": digest},
            )
        return digest
    blob_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return digest


def read_blob(digest: str, blob_dir: Path) -> dict[str, Any]:
    path = blob_path(blob_dir, digest)
    if not path.is_file():
        raise BlobError("blob not found", detail={"digest": digest, "path": str(path)})
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise BlobError("blob payload is not a JSON object", detail={"digest": digest})
    return loaded


def blob_path(blob_dir: Path, digest: str) -> Path:
    return blob_dir / digest
