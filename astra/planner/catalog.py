"""Capability-filtered tool catalog for the planner (FR-104)."""

from __future__ import annotations

from astra.core.types import Capability
from astra.tools.catalog import CatalogEntry


def filter_catalog(
    entries: list[CatalogEntry],
    *,
    ceiling: Capability,
) -> list[CatalogEntry]:
    return [entry for entry in entries if entry.base_capability <= ceiling]


def catalog_for_prompt(entries: list[CatalogEntry]) -> list[dict[str, object]]:
    return [
        {
            "name": entry.name,
            "description": entry.description,
            "capability": entry.base_capability.value,
            "input_schema": entry.input_schema,
        }
        for entry in entries
    ]
