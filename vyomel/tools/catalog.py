"""Tool catalog metadata exposed to the planner and API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vyomel.core.types import Capability


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    name: str
    version: str
    description: str
    base_capability: Capability
    reversible: bool
    idempotent: bool
    actuation_tier: int
    concurrency_key: str | None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
