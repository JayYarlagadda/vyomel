"""In-process fixture desktop backend for deterministic evals and CI."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from vyomel.core.errors import ErrorCode, ToolError
from vyomel.tools.desktop.resolve import _resolve_ref, _walk, resolve_element, tree_to_dict
from vyomel.tools.desktop.types import ElementRef, Target, UiNode, WindowSnapshot

_FIXTURE_SCHEME = "fixture://"


def _parse_bounds(raw: list[int] | None) -> tuple[int, int, int, int] | None:
    if not raw or len(raw) != 4:
        return None
    return raw[0], raw[1], raw[2], raw[3]


def _parse_node(raw: dict[str, Any]) -> UiNode:
    children = tuple(_parse_node(child) for child in raw.get("children", []))
    return UiNode(
        role=raw["role"],
        name=raw["name"],
        automation_id=raw.get("automation_id", ""),
        value=raw.get("value"),
        password=bool(raw.get("password", False)),
        children=children,
        bounds=_parse_bounds(raw.get("bounds")),
    )


def load_fixture(path: Path) -> tuple[str, UiNode]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    title = payload["title"]
    root = _parse_node(payload["root"])
    return title, root


@dataclass
class FixtureSession:
    fixtures_dir: Path
    title: str = ""
    tree: UiNode | None = None
    windows: list[str] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)

    def open_app(self, target: str) -> WindowSnapshot:
        title, tree = self._load_app(target)
        self.title = title
        self.tree = tree
        if title not in self.windows:
            self.windows.append(title)
        self.state = {
            "focused": title,
            "clicked": [],
            "field_values": {},
            "scroll": 0,
            "status": "ready",
        }
        return self.snapshot()

    def focus(self, title: str) -> WindowSnapshot:
        if title not in self.windows:
            raise ToolError(
                f"window not found: {title}",
                code=ErrorCode.PRECONDITION_FAILED,
                observation=title,
            )
        self.title = title
        self.state["focused"] = title
        return self.snapshot()

    def snapshot(self) -> WindowSnapshot:
        assert self.tree is not None
        return WindowSnapshot(title=self.title, tree=self.tree, state=dict(self.state))

    def list_windows(self) -> list[str]:
        return list(self.windows)

    def field_values(self) -> dict[str, str]:
        return dict(self.state.get("field_values", {}))

    def find(self, target: Target) -> ElementRef:
        assert self.tree is not None
        element, _tier = resolve_element(self.tree, target, values=self.field_values())
        return element

    def click_element(self, target: Target) -> dict[str, Any]:
        assert self.tree is not None
        element, tier = resolve_element(self.tree, target, values=self.field_values())
        node = _resolve_ref(self.tree, element.ref)[1]
        self.state.setdefault("clicked", []).append(element.name)
        if node.role.lower() == "button":
            if element.name.lower().startswith("export"):
                self.state["status"] = "exported"
            elif "submit" in element.name.lower() or "apply" in element.name.lower():
                self.state["status"] = "submitted"
            elif "save" in element.name.lower():
                self.state["status"] = "saved"
        return {"clicked": True, "ref": element.ref, "actuation_tier": tier}

    def set_field(self, target: Target, value: str) -> dict[str, Any]:
        assert self.tree is not None
        element, tier = resolve_element(self.tree, target, values=self.field_values())
        node = _resolve_ref(self.tree, element.ref)[1]
        if node.password:
            raise ToolError(
                "refusing to set a password field without explicit approval",
                code=ErrorCode.PERMISSION_DENIED,
                retryable=False,
            )
        self.state.setdefault("field_values", {})[element.ref] = value
        return {"value": value, "ref": element.ref, "actuation_tier": tier}

    def type_text(
        self,
        target: Target,
        text: str,
        *,
        allow_password: bool = False,
    ) -> dict[str, Any]:
        assert self.tree is not None
        element, tier = resolve_element(self.tree, target, values=self.field_values())
        node = _resolve_ref(self.tree, element.ref)[1]
        if node.password and not allow_password:
            raise ToolError(
                "refusing to type into a password field without explicit approval",
                code=ErrorCode.PERMISSION_DENIED,
                retryable=False,
            )
        self.state.setdefault("field_values", {})[element.ref] = text
        return {"typed": text, "ref": element.ref, "actuation_tier": tier}

    def key(self, keys: str) -> dict[str, Any]:
        normalized = keys.lower().replace(" ", "")
        if normalized in {"enter", "return"}:
            self.state["status"] = "submitted"
        elif normalized in {"ctrl+s", "control+s"}:
            self.state["status"] = "saved"
        return {"keys": keys, "status": self.state.get("status", "ready")}

    def scroll(self, *, direction: str, amount: int) -> dict[str, Any]:
        position = int(self.state.get("scroll", 0))
        delta = amount if direction == "down" else -amount
        self.state["scroll"] = max(0, position + delta)
        return {"scroll": self.state["scroll"]}

    def click_xy(self, x: int, y: int) -> dict[str, Any]:
        assert self.tree is not None
        element, _tier = resolve_element(
            self.tree,
            Target(x=x, y=y),
            values=self.field_values(),
        )
        node = _resolve_ref(self.tree, element.ref)[1]
        self.state.setdefault("clicked", []).append(element.name)
        if node.role.lower() == "button":
            if element.name.lower().startswith("export"):
                self.state["status"] = "exported"
            elif "submit" in element.name.lower() or "apply" in element.name.lower():
                self.state["status"] = "submitted"
            elif "save" in element.name.lower() or "complete" in element.name.lower():
                self.state["status"] = "saved"
        return {"clicked": True, "ref": element.ref, "actuation_tier": 4}

    def capture_evidence(self, path: Path) -> dict[str, Any]:
        assert self.tree is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "title": self.title,
            "tree": tree_to_dict(self.tree, values=self.field_values()),
            "state": self.state,
        }
        text = json.dumps(payload, indent=2)
        path.write_text(text, encoding="utf-8")
        return {"path": str(path), "bytes": len(text.encode("utf-8")), "actuation_tier": 4}

    def read_field(self, target: Target) -> dict[str, Any]:
        assert self.tree is not None
        element, tier = resolve_element(self.tree, target, values=self.field_values())
        values = self.field_values()
        value = values.get(element.ref, "")
        node = _resolve_ref(self.tree, element.ref)[1]
        if not value and node.value:
            value = node.value
        return {"value": value, "ref": element.ref, "actuation_tier": tier}

    def _load_app(self, target: str) -> tuple[str, UiNode]:
        parsed = urlparse(target)
        if parsed.scheme == "fixture":
            name = parsed.netloc or parsed.path.lstrip("/")
        else:
            name = target.strip("/").split("/")[-1] or "gradebook"
        path = self.fixtures_dir / f"{name}.json"
        if not path.is_file():
            raise ToolError(
                f"fixture app not found: {path}",
                code=ErrorCode.PRECONDITION_FAILED,
                observation=str(path),
            )
        return load_fixture(path)


def list_interactive(tree: UiNode) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for path, node in _walk(tree):
        if node.role.lower() in {"button", "edit", "combobox", "checkbox", "menuitem"}:
            items.append(
                {
                    "ref": path,
                    "role": node.role,
                    "name": node.name,
                    "automation_id": node.automation_id,
                }
            )
    return items
