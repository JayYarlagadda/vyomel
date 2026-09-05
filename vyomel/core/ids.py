"""Identifier generation.

ULIDs: lexicographically sortable by creation time, URL-safe, 26 chars. Sorting
by primary key gives chronological order for free, which matters for audit
traversal and for reconstructing task timelines without a secondary index.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ulid import ULID


def new_id() -> str:
    return str(ULID())


def canonical_json(value: Any) -> str:
    """Stable JSON encoding: sorted keys, no incidental whitespace.

    Used wherever a hash must be reproducible across processes and runs --
    idempotency keys, audit hash chaining, and the model response cache.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    """SHA-256 of the file's bytes. Used by writers and by the verifier.

    Both sides must hash the same way: the verifier re-opens the file rather
    than trusting a tool-reported digest, but the algorithm has to match or
    every honest write would fail its own postcondition.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65_536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def idempotency_key(
    *, tool: str, parameters: dict[str, Any], task_id: str, step_id: str, plan_version: int
) -> str:
    """Deterministic across replays of one logical action, distinct across others.

    See docs/03-DATA-MODEL.md section 3.3. Replaying an action after a crash
    reproduces this key exactly, which is what lets the runtime recognize the
    replay and skip re-executing the side effect.
    """
    return content_hash(
        {
            "tool": tool,
            "parameters": parameters,
            "task_id": task_id,
            "step_id": step_id,
            "plan_version": plan_version,
        }
    )
