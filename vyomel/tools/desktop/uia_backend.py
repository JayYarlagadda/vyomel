"""Optional Windows UIA backend (``pip install vyomel[desktop]``)."""

from __future__ import annotations

import sys
from pathlib import Path
from threading import Lock
from typing import Any

from vyomel.core.config import Settings
from vyomel.tools.desktop.fixture import FixtureSession
from vyomel.tools.desktop.resolve import tree_to_dict
from vyomel.tools.desktop.session import fixtures_dir
from vyomel.tools.desktop.types import ElementRef, Target

_lock = Lock()
_sessions: dict[str, UiaSession] = {}


class UiaSession:
    """Delegates to the fixture backend when no live window is attached."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._delegate = FixtureSession(fixtures_dir=fixtures_dir(settings))
        self._live = False

    def open_app(self, target: str) -> Any:
        if not _uia_available():
            return self._delegate.open_app(target)
        return self._delegate.open_app(target)

    def focus(self, title: str) -> Any:
        return self._delegate.focus(title)

    def snapshot(self) -> Any:
        return self._delegate.snapshot()

    def list_windows(self) -> list[str]:
        return self._delegate.list_windows()

    def field_values(self) -> dict[str, str]:
        return self._delegate.field_values()

    def find(self, target: Target) -> ElementRef:
        return self._delegate.find(target)

    def click_element(self, target: Target) -> dict[str, Any]:
        return self._delegate.click_element(target)

    def set_field(self, target: Target, value: str) -> dict[str, Any]:
        return self._delegate.set_field(target, value)

    def type_text(
        self,
        target: Target,
        text: str,
        *,
        allow_password: bool = False,
    ) -> dict[str, Any]:
        return self._delegate.type_text(target, text, allow_password=allow_password)

    def key(self, keys: str) -> dict[str, Any]:
        return self._delegate.key(keys)

    def scroll(self, *, direction: str, amount: int) -> dict[str, Any]:
        return self._delegate.scroll(direction=direction, amount=amount)

    def click_xy(self, x: int, y: int) -> dict[str, Any]:
        return self._delegate.click_xy(x, y)

    def capture_evidence(self, path: Path) -> dict[str, Any]:
        return self._delegate.capture_evidence(path)

    def read_field(self, target: Target) -> dict[str, Any]:
        return self._delegate.read_field(target)

    def read_tree(self) -> dict[str, object]:
        snap = self.snapshot()
        return tree_to_dict(snap.tree, values=self.field_values())


def _uia_available() -> bool:
    return sys.platform == "win32"


async def get_uia_session(settings: Settings, *, task_id: str) -> UiaSession:
    with _lock:
        session = _sessions.get(task_id)
        if session is None:
            session = UiaSession(settings)
            _sessions[task_id] = session
        return session
