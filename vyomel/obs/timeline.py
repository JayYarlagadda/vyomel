"""Task timeline tree (docs/10 §6). Built from persisted task/step/action rows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class TraceNode:
    name: str
    status: str | None = None
    duration_s: float | None = None
    detail: str = ""
    excluded_from_task_time: bool = False
    children: tuple[TraceNode, ...] = ()
    attributes: dict[str, Any] = field(default_factory=dict)


def duration_seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds(), 3)


def render_timeline(root: TraceNode) -> str:
    status = f"{root.status:12}" if root.status else " " * 12
    dur = _dur(root.duration_s)
    header = f"Task {root.name:<48} {status} {dur}".rstrip()
    lines = [header]
    children = root.children
    for index, child in enumerate(children):
        last = index == len(children) - 1
        lines.extend(_render_child(child, prefix="", is_last=last))
    return "\n".join(lines)


def _render_child(node: TraceNode, *, prefix: str, is_last: bool) -> list[str]:
    branch = "└─ " if is_last else "├─ "
    child_prefix = prefix + ("   " if is_last else "│  ")
    status = f"{node.status:12}" if node.status else " " * 12
    extra = f"  [{node.detail}]" if node.detail and node.excluded_from_task_time else ""
    if node.detail and not node.excluded_from_task_time:
        label = f"{node.name} {node.detail}".rstrip()
    else:
        label = node.name
    note = "  [excluded from task time]" if node.excluded_from_task_time else extra
    line = f"{prefix}{branch}{label:<46} {status} {_dur(node.duration_s)}{note}".rstrip()
    lines = [line]
    for index, child in enumerate(node.children):
        lines.extend(
            _render_child(child, prefix=child_prefix, is_last=index == len(node.children) - 1)
        )
    return lines


def _dur(value: float | None) -> str:
    if value is None:
        return ""
    if value < 10:
        return f"{value:5.1f}s"
    return f"{value:5.1f}s"
