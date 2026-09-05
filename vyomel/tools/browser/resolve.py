"""Accessibility-first element resolution (docs/05 §3.3, ADR-0004)."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from vyomel.tools.browser.metrics import record_actuation_tier
from vyomel.tools.browser.types import A11yNode, ActuationTier, ElementRef, Target

_ROLE_FOR_TAG: dict[str, str] = {
    "button": "button",
    "a": "link",
    "input": "textbox",
    "select": "combobox",
    "textarea": "textbox",
    "h1": "heading",
    "h2": "heading",
    "h3": "heading",
    "table": "table",
    "tr": "row",
    "td": "cell",
    "th": "columnheader",
}


class _DomNode:
    __slots__ = ("tag", "attrs", "children", "text")

    def __init__(self) -> None:
        self.tag = ""
        self.attrs: dict[str, str] = {}
        self.children: list[_DomNode] = []
        self.text = ""


class _DomParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.root = _DomNode()
        self._stack: list[_DomNode] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _DomNode()
        node.tag = tag.lower()
        node.attrs = {key: value or "" for key, value in attrs}
        self._stack[-1].children.append(node)
        if tag.lower() not in {"input", "img", "br", "hr", "meta", "link"}:
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        if len(self._stack) > 1 and self._stack[-1].tag == tag.lower():
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        self._stack[-1].text += data


def parse_dom(html: str) -> _DomNode:
    parser = _DomParser()
    parser.feed(html)
    return parser.root


def _node_name(node: _DomNode) -> str:
    for key in ("aria-label", "name", "id", "placeholder", "value"):
        if node.attrs.get(key):
            return node.attrs[key]
    text = re.sub(r"\s+", " ", node.text).strip()
    if text:
        return text[:120]
    return node.attrs.get("data-testid", node.tag)


def _node_role(node: _DomNode) -> str:
    explicit = node.attrs.get("role")
    if explicit:
        return explicit
    if node.tag == "input":
        input_type = node.attrs.get("type", "text").lower()
        if input_type == "password":
            return "textbox"
        if input_type == "submit":
            return "button"
        return "textbox"
    return _ROLE_FOR_TAG.get(node.tag, node.tag)


def build_a11y_tree(node: _DomNode) -> A11yNode:
    role = _node_role(node)
    name = _node_name(node)
    value = node.attrs.get("value") or None
    children = tuple(build_a11y_tree(child) for child in node.children if child.tag)
    if not node.tag:
        if len(children) == 1:
            return children[0]
        return A11yNode(role="document", name="page", children=children)
    if not name and not children:
        return A11yNode(role=role, name=role, value=value)
    return A11yNode(role=role, name=name, value=value, children=children)


def _walk(node: _DomNode, path: str = "0") -> list[tuple[str, _DomNode]]:
    found: list[tuple[str, _DomNode]] = [(path, node)]
    for index, child in enumerate(node.children):
        found.extend(_walk(child, f"{path}.{index}"))
    return found


def _match_target(node: _DomNode, target: Target) -> bool:
    if target.ref is not None:
        return False
    role = _node_role(node)
    name = _node_name(node)
    if target.role and target.role.lower() != role.lower():
        return False
    if target.name and target.name.lower() not in name.lower():
        return False
    if target.selector:
        selector_id = target.selector.lstrip("#")
        if node.attrs.get("id") != selector_id and node.attrs.get("class") != selector_id:
            if f"#{node.attrs.get('id', '')}" != target.selector:
                if target.selector not in node.attrs.get("class", ""):
                    return False
    return True


def resolve_element(dom: _DomNode, target: Target) -> tuple[ElementRef, ActuationTier]:
    if target.ref is not None:
        record_actuation_tier(2)
        path, node = _resolve_ref(dom, target.ref)
        return (
            ElementRef(
                ref=path,
                role=_node_role(node),
                name=_node_name(node),
                actuation_tier=2,
            ),
            2,
        )
    for tier, resolver in (
        (2, _resolve_a11y),
        (3, _resolve_dom),
        (4, _resolve_coordinates),
    ):
        match = resolver(dom, target)
        if match is not None:
            path, node = match
            record_actuation_tier(tier)
            return (
                ElementRef(
                    ref=path,
                    role=_node_role(node),
                    name=_node_name(node),
                    actuation_tier=tier,  # type: ignore[arg-type]
                ),
                tier,  # type: ignore[return-value]
            )
    raise LookupError("element not found")


def _resolve_ref(dom: _DomNode, ref: str) -> tuple[str, _DomNode]:
    for path, node in _walk(dom):
        if path == ref:
            return path, node
    raise LookupError(f"unknown element ref {ref!r}")


def _resolve_a11y(dom: _DomNode, target: Target) -> tuple[str, _DomNode] | None:
    if not target.role and not target.name:
        return None
    for path, node in _walk(dom):
        if node.tag and _match_target(node, target):
            return path, node
    return None


def _resolve_dom(dom: _DomNode, target: Target) -> tuple[str, _DomNode] | None:
    if not target.selector:
        return None
    for path, node in _walk(dom):
        if not node.tag:
            continue
        node_id = node.attrs.get("id")
        if target.selector == f"#{node_id}" or target.selector == node_id:
            return path, node
        classes = node.attrs.get("class", "")
        if target.selector in classes.split():
            return path, node
    return None


def _resolve_coordinates(dom: _DomNode, target: Target) -> tuple[str, _DomNode] | None:
    if target.x is None or target.y is None:
        return None
    # Fixture backend: coordinates map to the first interactive element.
    for path, node in _walk(dom):
        if node.tag in {"button", "a", "input", "select", "textarea"}:
            return path, node
    return None


def dom_excerpt(dom: _DomNode, *, limit: int = 2_000) -> str:
    parts: list[str] = []

    def visit(node: _DomNode) -> None:
        if sum(len(part) for part in parts) > limit:
            return
        if node.tag:
            attrs = " ".join(f'{key}="{value}"' for key, value in node.attrs.items())
            open_tag = f"<{node.tag} {attrs}>".strip()
            parts.append(open_tag)
        for child in node.children:
            visit(child)

    visit(dom)
    text = "".join(parts)
    return text[:limit]
