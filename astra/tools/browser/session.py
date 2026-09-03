"""Browser session management."""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING

from astra.core.config import Settings
from astra.tools.browser.fixture import FixtureSession

if TYPE_CHECKING:
    from astra.tools.browser.playwright_backend import PlaywrightSession

_lock = Lock()
_fixture_sessions: dict[str, FixtureSession] = {}
_playwright_sessions: dict[str, PlaywrightSession] = {}


def fixtures_dir(settings: Settings) -> Path:
    if settings.browser_fixtures_dir.is_absolute():
        return settings.browser_fixtures_dir
    return Path.cwd() / settings.browser_fixtures_dir


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
        _playwright_sessions.clear()


def backend_name(settings: Settings) -> str:
    mode = settings.browser_backend
    if mode == "fixture":
        return "fixture"
    if mode == "playwright":
        return "playwright"
    try:
        import playwright  # noqa: F401

        return "playwright"
    except ImportError:
        return "fixture"


async def get_session(settings: Settings, *, task_id: str) -> FixtureSession | PlaywrightSession:
    if backend_name(settings) == "playwright":
        from astra.tools.browser.playwright_backend import get_playwright_session

        return await get_playwright_session(settings, task_id=task_id)
    return get_fixture_session(settings, task_id=task_id)
