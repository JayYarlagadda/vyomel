"""Desktop session management."""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING

from vyomel.core.config import Settings
from vyomel.tools.desktop.fixture import FixtureSession

if TYPE_CHECKING:
    from vyomel.tools.desktop.uia_backend import UiaSession

_lock = Lock()
_fixture_sessions: dict[str, FixtureSession] = {}
_uia_sessions: dict[str, UiaSession] = {}


def fixtures_dir(settings: Settings) -> Path:
    if settings.desktop_fixtures_dir.is_absolute():
        return settings.desktop_fixtures_dir
    return Path.cwd() / settings.desktop_fixtures_dir


def get_fixture_session(settings: Settings, *, task_id: str) -> FixtureSession:
    with _lock:
        session = _fixture_sessions.get(task_id)
        if session is None:
            session = FixtureSession(fixtures_dir=fixtures_dir(settings))
            _fixture_sessions[task_id] = session
        return session


def reset_sessions() -> None:
    with _lock:
        _fixture_sessions.clear()
        _uia_sessions.clear()


def backend_name(settings: Settings) -> str:
    mode = settings.desktop_backend
    if mode == "fixture":
        return "fixture"
    if mode == "uia":
        return "uia"
    try:
        import uiautomation  # noqa: F401

        return "uia"
    except ImportError:
        return "fixture"


async def get_session(settings: Settings, *, task_id: str) -> FixtureSession | UiaSession:
    if backend_name(settings) == "uia":
        from vyomel.tools.desktop.uia_backend import get_uia_session

        return await get_uia_session(settings, task_id=task_id)
    return get_fixture_session(settings, task_id=task_id)
