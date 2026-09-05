"""UIA-first element resolution (docs/05 §3.4, ADR-0005)."""

from __future__ import annotations

from vyomel.tools.desktop.metrics import record_actuation_tier
from vyomel.tools.desktop.types import ActuationTier, ElementRef, Target, UiNode


def _walk(node: UiNode, path: str = "0") -> list[tuple[str, UiNode]]:
    found: list[tuple[str, UiNode]] = [(path, node)]
    for index, child in enumerate(node.children):
        found.extend(_walk(child, f"{path}.{index}"))
    return found


def _node_value(path: str, node: UiNode, values: dict[str, str]) -> str | None:
    if path in values:
        return values[path]
    return node.value


def _match_uia(node: UiNode, target: Target) -> bool:
    if target.role and target.role.lower() != node.role.lower():
        return False
    return not target.name or target.name.lower() in node.name.lower()


def _match_automation_id(node: UiNode, target: Target) -> bool:
    if not target.automation_id:
        return False
    return node.automation_id == target.automation_id


def resolve_element(
    root: UiNode,
    target: Target,
    *,
    values: dict[str, str] | None = None,
) -> tuple[ElementRef, ActuationTier]:
    values = values or {}
    if target.ref is not None:
        record_actuation_tier(2)
        path, node = _resolve_ref(root, target.ref)
        return _element_ref(path, node, 2), 2

    for tier, resolver in (
        (2, _resolve_uia),
        (3, _resolve_automation_id),
        (4, _resolve_coordinates),
    ):
        match = resolver(root, target)
        if match is not None:
            path, node = match
            record_actuation_tier(tier)
            return _element_ref(path, node, tier), tier  # type: ignore[return-value]
    raise LookupError("element not found")


def _element_ref(path: str, node: UiNode, tier: ActuationTier) -> ElementRef:
    return ElementRef(
        ref=path,
        role=node.role,
        name=node.name,
        automation_id=node.automation_id,
        actuation_tier=tier,
    )


def _resolve_ref(root: UiNode, ref: str) -> tuple[str, UiNode]:
    for path, node in _walk(root):
        if path == ref:
            return path, node
    raise LookupError(f"unknown element ref {ref!r}")


def _resolve_uia(root: UiNode, target: Target) -> tuple[str, UiNode] | None:
    if not target.role and not target.name:
        return None
    for path, node in _walk(root):
        if _match_uia(node, target):
            return path, node
    return None


def _resolve_automation_id(root: UiNode, target: Target) -> tuple[str, UiNode] | None:
    if not target.automation_id:
        return None
    for path, node in _walk(root):
        if _match_automation_id(node, target):
            return path, node
    return None


def _resolve_coordinates(root: UiNode, target: Target) -> tuple[str, UiNode] | None:
    if target.x is None or target.y is None:
        return None
    for path, node in _walk(root):
        if node.bounds is None:
            continue
        x, y, width, height = node.bounds
        if x <= target.x <= x + width and y <= target.y <= y + height:
            return path, node
    return None


def tree_to_dict(
    node: UiNode,
    *,
    values: dict[str, str] | None = None,
    path: str = "0",
) -> dict[str, object]:
    values = values or {}
    return {
        "role": node.role,
        "name": node.name,
        "automation_id": node.automation_id,
        "value": _node_value(path, node, values),
        "children": [
            tree_to_dict(child, values=values, path=f"{path}.{index}")
            for index, child in enumerate(node.children)
        ],
    }
