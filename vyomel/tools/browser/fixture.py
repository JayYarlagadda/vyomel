"""In-process fixture browser for deterministic evals and CI."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from vyomel.core.errors import ErrorCode, ToolError
from vyomel.tools.browser.resolve import (
    _node_name,
    _node_role,
    _resolve_ref,
    _walk,
    build_a11y_tree,
    dom_excerpt,
    parse_dom,
    resolve_element,
)
from vyomel.tools.browser.types import ElementRef, PageSnapshot, Target

_FIXTURE_SCHEME = "fixture://"


@dataclass
class FixtureSession:
    fixtures_dir: Path
    url: str = ""
    html: str = ""
    dom: Any = field(default=None, repr=False)
    state: dict[str, Any] = field(default_factory=dict)

    def open(self, url: str) -> PageSnapshot:
        self.url = url
        self.html = self._load_html(url)
        self.dom = parse_dom(self.html)
        self.state = {"submitted": False, "clicked": [], "typed": {}}
        return self.snapshot()

    def snapshot(self) -> PageSnapshot:
        assert self.dom is not None
        title_match = re.search(r"<title>(.*?)</title>", self.html, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else self.url
        return PageSnapshot(
            url=self.url,
            title=title,
            a11y_tree=build_a11y_tree(self.dom),
            dom_excerpt=dom_excerpt(self.dom),
            state=dict(self.state),
        )

    def query(self, target: Target) -> ElementRef:
        assert self.dom is not None
        element, _tier = resolve_element(self.dom, target)
        return element

    def click(self, target: Target) -> dict[str, Any]:
        assert self.dom is not None
        element, tier = resolve_element(self.dom, target)
        node = _resolve_ref(self.dom, element.ref)[1]
        self.state.setdefault("clicked", []).append(element.name)
        if node.tag == "button" or node.attrs.get("type") == "submit":
            self.state["submitted"] = node.attrs.get("data-submit", "true") == "true"
        return {"clicked": True, "ref": element.ref, "actuation_tier": tier}

    def type_text(self, target: Target, text: str, *, allow_password: bool) -> dict[str, Any]:
        assert self.dom is not None
        element, tier = resolve_element(self.dom, target)
        node = _resolve_ref(self.dom, element.ref)[1]
        if node.tag == "input" and node.attrs.get("type", "").lower() == "password":
            if not allow_password:
                raise ToolError(
                    "refusing to type into a password field without explicit approval",
                    code=ErrorCode.PERMISSION_DENIED,
                    retryable=False,
                )
        key = element.ref
        self.state.setdefault("typed", {})[key] = text
        node.attrs["value"] = text
        return {"typed": text, "ref": element.ref, "actuation_tier": tier}

    def select(self, target: Target, value: str) -> dict[str, Any]:
        assert self.dom is not None
        element, tier = resolve_element(self.dom, target)
        node = _resolve_ref(self.dom, element.ref)[1]
        node.attrs["value"] = value
        self.state.setdefault("selected", {})[element.ref] = value
        return {"selected": value, "ref": element.ref, "actuation_tier": tier}

    def scroll(self, *, direction: str, amount: int) -> dict[str, Any]:
        position = int(self.state.get("scroll", 0))
        delta = amount if direction == "down" else -amount
        self.state["scroll"] = max(0, position + delta)
        return {"scroll": self.state["scroll"]}

    def submit(self, target: Target | None = None) -> dict[str, Any]:
        if target is not None:
            self.click(target)
        self.state["submitted"] = True
        return {"submitted": True}

    def screenshot(self, path: Path) -> dict[str, Any]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.html, encoding="utf-8")
        return {"path": str(path), "bytes": path.stat().st_size}

    def download(self, target: Target, dest: Path) -> dict[str, Any]:
        assert self.dom is not None
        element, tier = resolve_element(self.dom, target)
        node = _resolve_ref(self.dom, element.ref)[1]
        href = node.attrs.get("href", "download.txt")
        content = f"fixture download for {href}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        return {"path": str(dest), "bytes": len(content), "actuation_tier": tier}

    def _load_html(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme == "fixture":
            name = parsed.netloc or parsed.path.lstrip("/")
            path = self.fixtures_dir / f"{name}.html"
        elif parsed.scheme == "file":
            path = Path(unquote(parsed.path.lstrip("/")))
            if path.drive == "" and len(parsed.path) > 2 and parsed.path[2] == ":":
                path = Path(parsed.path[1:])
        else:
            name = parsed.path.strip("/").split("/")[-1] or "job_board"
            path = self.fixtures_dir / f"{name}.html"
        if not path.is_file():
            raise ToolError(
                f"fixture page not found: {path}",
                code=ErrorCode.PRECONDITION_FAILED,
                observation=str(path),
            )
        return path.read_text(encoding="utf-8")


def a11y_to_dict(node: Any) -> dict[str, Any]:
    return {
        "role": node.role,
        "name": node.name,
        "value": node.value,
        "children": [a11y_to_dict(child) for child in node.children],
    }


def list_interactive(dom: Any) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for path, node in _walk(dom):
        if node.tag in {"button", "a", "input", "select", "textarea"}:
            items.append(
                {
                    "ref": path,
                    "role": _node_role(node),
                    "name": _node_name(node),
                }
            )
    return items
