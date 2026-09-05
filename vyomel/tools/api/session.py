"""API session: fixture backend plus OAuth token store."""

from __future__ import annotations

from threading import Lock

from vyomel.core.clock import Clock, SystemClock
from vyomel.core.config import Settings
from vyomel.core.oauth import TokenStore, get_token_store, issue_token, reset_memory_store
from vyomel.tools.api.fixture import FixtureApi

_lock = Lock()
_sessions: dict[str, FixtureApi] = {}


def get_token_store_for(settings: Settings) -> TokenStore:
    return get_token_store(backend=settings.oauth_backend, workspace_root=settings.workspace_root)


def get_api(settings: Settings, *, task_id: str, clock: Clock | None = None) -> FixtureApi:
    with _lock:
        session = _sessions.get(task_id)
        if session is None:
            allow = frozenset(h.strip() for h in settings.api_allow_hosts if h.strip())
            session = FixtureApi(clock=clock or SystemClock(), allow_hosts=allow)
            _sessions[task_id] = session
        return session


def reset_api_sessions() -> None:
    with _lock:
        _sessions.clear()
    reset_memory_store()


def login_fixture(settings: Settings, provider: str, account: str = "default") -> None:
    from vyomel.core.oauth import PROVIDER_SCOPES

    store = get_token_store_for(settings)
    scopes = set(PROVIDER_SCOPES[provider])
    store.put(issue_token(provider, account, scopes))
