"""OAuth tokens (FR-606).

Tokens live in the OS keyring in production, never in Postgres or ``.env``.
Refresh rotation invalidates the previous refresh token so a stolen one cannot
be reused after a legitimate refresh.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Protocol

from vyomel.core.errors import ErrorCode, ToolError

# Least-privilege scopes. A tool asks for one of these, never ``https://mail.google.com/``.
GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
CALENDAR_READONLY = "https://www.googleapis.com/auth/calendar.readonly"
CALENDAR_EVENTS = "https://www.googleapis.com/auth/calendar.events"
GITHUB_READ = "public_repo"
GITHUB_WRITE = "repo"

PROVIDER_SCOPES: dict[str, frozenset[str]] = {
    "google": frozenset(
        {GMAIL_READONLY, GMAIL_COMPOSE, GMAIL_SEND, CALENDAR_READONLY, CALENDAR_EVENTS}
    ),
    "github": frozenset({GITHUB_READ, GITHUB_WRITE}),
}

TOOL_SCOPES: dict[str, tuple[str, str]] = {
    "email.search": ("google", GMAIL_READONLY),
    "email.read": ("google", GMAIL_READONLY),
    "email.draft": ("google", GMAIL_COMPOSE),
    "email.send": ("google", GMAIL_SEND),
    "calendar.list": ("google", CALENDAR_READONLY),
    "calendar.find_free": ("google", CALENDAR_READONLY),
    "calendar.create_event": ("google", CALENDAR_EVENTS),
    "calendar.delete_event": ("google", CALENDAR_EVENTS),
    "github.search": ("github", GITHUB_READ),
    "github.read": ("github", GITHUB_READ),
    "github.create_issue": ("github", GITHUB_WRITE),
    "github.comment": ("github", GITHUB_WRITE),
}

_KEYRING_SERVICE = "vyomel.oauth"


@dataclass(frozen=True, slots=True)
class OAuthToken:
    provider: str
    account: str
    access_token: str
    refresh_token: str
    scopes: frozenset[str]
    expires_at: datetime
    rotated_at: datetime

    def has_scope(self, scope: str) -> bool:
        if scope in self.scopes:
            return True
        # GitHub ``repo`` includes ``public_repo``.
        return scope == GITHUB_READ and GITHUB_WRITE in self.scopes

    def expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    def to_payload(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "account": self.account,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "scopes": sorted(self.scopes),
            "expires_at": self.expires_at.isoformat(),
            "rotated_at": self.rotated_at.isoformat(),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> OAuthToken:
        raw_scopes = payload["scopes"]
        if not isinstance(raw_scopes, list):
            raise ToolError("token payload scopes must be a list", code=ErrorCode.INTERNAL)
        return cls(
            provider=str(payload["provider"]),
            account=str(payload["account"]),
            access_token=str(payload["access_token"]),
            refresh_token=str(payload["refresh_token"]),
            scopes=frozenset(str(item) for item in raw_scopes),
            expires_at=datetime.fromisoformat(str(payload["expires_at"])),
            rotated_at=datetime.fromisoformat(str(payload["rotated_at"])),
        )


class TokenStore(Protocol):
    def get(self, provider: str, account: str) -> OAuthToken | None: ...

    def put(self, token: OAuthToken) -> None: ...

    def delete(self, provider: str, account: str) -> bool: ...

    def list_accounts(self) -> list[tuple[str, str]]: ...


class MemoryTokenStore:
    """In-process store for tests and the fixture backend."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._tokens: dict[tuple[str, str], OAuthToken] = {}
        self._retired_refresh: set[str] = set()

    def get(self, provider: str, account: str) -> OAuthToken | None:
        with self._lock:
            return self._tokens.get((provider, account))

    def put(self, token: OAuthToken) -> None:
        with self._lock:
            previous = self._tokens.get((token.provider, token.account))
            if previous is not None:
                self._retired_refresh.add(previous.refresh_token)
            self._tokens[(token.provider, token.account)] = token

    def delete(self, provider: str, account: str) -> bool:
        with self._lock:
            return self._tokens.pop((provider, account), None) is not None

    def list_accounts(self) -> list[tuple[str, str]]:
        with self._lock:
            return sorted(self._tokens)

    def refresh_retired(self, refresh_token: str) -> bool:
        with self._lock:
            return refresh_token in self._retired_refresh


_memory = MemoryTokenStore()


class FileTokenStore:
    """JSON file under the workspace. Used when keyring is not installed."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()
        self._retired: set[str] = set()

    def get(self, provider: str, account: str) -> OAuthToken | None:
        tokens = self._load()
        payload = tokens.get(f"{provider}:{account}")
        if payload is None:
            return None
        return OAuthToken.from_payload(payload)

    def put(self, token: OAuthToken) -> None:
        with self._lock:
            tokens = self._load_unlocked()
            key = f"{token.provider}:{token.account}"
            previous = tokens.get(key)
            if previous is not None:
                self._retired.add(str(previous["refresh_token"]))
            tokens[key] = token.to_payload()
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(tokens, indent=2), encoding="utf-8")

    def delete(self, provider: str, account: str) -> bool:
        with self._lock:
            tokens = self._load_unlocked()
            removed = tokens.pop(f"{provider}:{account}", None) is not None
            if removed:
                self._path.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
            return removed

    def list_accounts(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for key in self._load():
            provider, _, account = key.partition(":")
            out.append((provider, account))
        return sorted(out)

    def _load(self) -> dict[str, dict[str, object]]:
        with self._lock:
            return self._load_unlocked()

    def _load_unlocked(self) -> dict[str, dict[str, object]]:
        if not self._path.is_file():
            return {}
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        loaded: dict[str, dict[str, object]] = {}
        for key, value in raw.items():
            if isinstance(key, str) and isinstance(value, dict):
                loaded[key] = value
        return loaded


class KeyringTokenStore:
    def __init__(self, service: str = _KEYRING_SERVICE) -> None:
        try:
            import keyring
        except ImportError as exc:
            raise ToolError(
                "keyring is not installed; pip install vyomel[api]",
                code=ErrorCode.PRECONDITION_FAILED,
            ) from exc
        self._keyring = keyring
        self._service = service
        self._index_user = "_accounts"

    def get(self, provider: str, account: str) -> OAuthToken | None:
        raw = self._keyring.get_password(self._service, f"{provider}:{account}")
        if raw is None:
            return None
        return OAuthToken.from_payload(json.loads(raw))

    def put(self, token: OAuthToken) -> None:
        username = f"{token.provider}:{token.account}"
        self._keyring.set_password(self._service, username, json.dumps(token.to_payload()))
        accounts = self.list_accounts()
        if (token.provider, token.account) not in accounts:
            accounts.append((token.provider, token.account))
            self._keyring.set_password(
                self._service,
                self._index_user,
                json.dumps([f"{p}:{a}" for p, a in accounts]),
            )

    def delete(self, provider: str, account: str) -> bool:
        username = f"{provider}:{account}"
        existing = self._keyring.get_password(self._service, username)
        if existing is None:
            return False
        self._keyring.delete_password(self._service, username)
        remaining = [(p, a) for p, a in self.list_accounts() if (p, a) != (provider, account)]
        self._keyring.set_password(
            self._service,
            self._index_user,
            json.dumps([f"{p}:{a}" for p, a in remaining]),
        )
        return True

    def list_accounts(self) -> list[tuple[str, str]]:
        raw = self._keyring.get_password(self._service, self._index_user)
        if not raw:
            return []
        items = json.loads(raw)
        out: list[tuple[str, str]] = []
        for item in items:
            provider, _, account = str(item).partition(":")
            out.append((provider, account))
        return sorted(out)


def issue_token(
    provider: str,
    account: str,
    scopes: set[str] | frozenset[str],
    *,
    now: datetime | None = None,
    ttl: timedelta = timedelta(hours=1),
) -> OAuthToken:
    allowed = PROVIDER_SCOPES.get(provider)
    if allowed is None:
        raise ToolError(
            f"unknown oauth provider {provider!r}",
            code=ErrorCode.INVALID_PARAMETERS,
            retryable=False,
        )
    extra = set(scopes) - set(allowed)
    if extra:
        raise ToolError(
            f"scope {sorted(extra)} exceeds least-privilege set for {provider}",
            code=ErrorCode.PERMISSION_DENIED,
            retryable=False,
        )
    moment = now or datetime.now(UTC)
    return OAuthToken(
        provider=provider,
        account=account,
        access_token=f"at_{secrets.token_urlsafe(16)}",
        refresh_token=f"rt_{secrets.token_urlsafe(16)}",
        scopes=frozenset(scopes),
        expires_at=moment + ttl,
        rotated_at=moment,
    )


def rotate_refresh(
    store: TokenStore,
    token: OAuthToken,
    *,
    now: datetime | None = None,
    presented_refresh: str | None = None,
) -> OAuthToken:
    """Issue a new access+refresh pair. The previous refresh token is spent."""
    if presented_refresh is not None and presented_refresh != token.refresh_token:
        raise ToolError(
            "refresh token has been rotated and is no longer valid",
            code=ErrorCode.PERMISSION_DENIED,
            retryable=False,
        )
    if (
        isinstance(store, MemoryTokenStore)
        and presented_refresh is not None
        and store.refresh_retired(presented_refresh)
    ):
        raise ToolError(
            "refresh token has been rotated and is no longer valid",
            code=ErrorCode.PERMISSION_DENIED,
            retryable=False,
        )
    moment = now or datetime.now(UTC)
    rotated = OAuthToken(
        provider=token.provider,
        account=token.account,
        access_token=f"at_{secrets.token_urlsafe(16)}",
        refresh_token=f"rt_{secrets.token_urlsafe(16)}",
        scopes=token.scopes,
        expires_at=moment + timedelta(hours=1),
        rotated_at=moment,
    )
    store.put(rotated)
    return rotated


def require_token(
    store: TokenStore,
    tool: str,
    *,
    account: str = "default",
    now: datetime | None = None,
) -> OAuthToken:
    mapping = TOOL_SCOPES.get(tool)
    if mapping is None:
        raise ToolError(
            f"{tool} is not an oauth-backed tool",
            code=ErrorCode.INTERNAL,
        )
    provider, scope = mapping
    token = store.get(provider, account)
    if token is None:
        raise ToolError(
            f"not authenticated for {provider}; run `vyomel auth login {provider}`",
            code=ErrorCode.PERMISSION_DENIED,
            retryable=False,
        )
    moment = now or datetime.now(UTC)
    if token.expired(moment):
        token = rotate_refresh(store, token, now=moment)
    if not token.has_scope(scope):
        raise ToolError(
            f"token for {provider} lacks scope {scope}",
            code=ErrorCode.PERMISSION_DENIED,
            retryable=False,
            observation=scope,
        )
    return token


def reset_memory_store() -> None:
    global _memory
    _memory = MemoryTokenStore()


def get_token_store(*, backend: str, workspace_root: Path | None = None) -> TokenStore:
    if backend == "memory":
        return _memory
    if backend == "file":
        assert workspace_root is not None
        return FileTokenStore(workspace_root / "oauth" / "tokens.json")
    if backend == "keyring":
        return KeyringTokenStore()
    if backend == "auto":
        try:
            import keyring  # noqa: F401

            return KeyringTokenStore()
        except ImportError:
            if workspace_root is not None:
                return FileTokenStore(workspace_root / "oauth" / "tokens.json")
            return _memory
    raise ToolError(f"unknown oauth backend {backend!r}", code=ErrorCode.INVALID_PARAMETERS)
