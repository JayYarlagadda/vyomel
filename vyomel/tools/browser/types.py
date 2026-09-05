"""Browser automation types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ActuationTier = Literal[1, 2, 3, 4]


@dataclass(frozen=True, slots=True)
class ElementRef:
    """Stable handle returned by ``browser.query``."""

    ref: str
    role: str
    name: str
    actuation_tier: ActuationTier


@dataclass(frozen=True, slots=True)
class A11yNode:
    role: str
    name: str
    value: str | None = None
    children: tuple[A11yNode, ...] = ()


@dataclass(slots=True)
class PageSnapshot:
    url: str
    title: str
    a11y_tree: A11yNode
    dom_excerpt: str
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Target:
    """Resolution target: accessibility first, DOM second, coordinates last."""

    role: str | None = None
    name: str | None = None
    selector: str | None = None
    ref: str | None = None
    x: int | None = None
    y: int | None = None
