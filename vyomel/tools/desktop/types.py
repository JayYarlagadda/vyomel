"""Desktop automation types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ActuationTier = Literal[1, 2, 3, 4]


@dataclass(frozen=True, slots=True)
class ElementRef:
    """Stable handle returned by ``desktop.find_element``."""

    ref: str
    role: str
    name: str
    automation_id: str
    actuation_tier: ActuationTier


@dataclass(frozen=True, slots=True)
class UiNode:
    role: str
    name: str
    automation_id: str = ""
    value: str | None = None
    password: bool = False
    children: tuple[UiNode, ...] = ()
    bounds: tuple[int, int, int, int] | None = None


@dataclass(slots=True)
class WindowSnapshot:
    title: str
    tree: UiNode
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Target:
    """Resolution target: UIA role/name first, automation-id second, coordinates last."""

    role: str | None = None
    name: str | None = None
    automation_id: str | None = None
    ref: str | None = None
    x: int | None = None
    y: int | None = None
