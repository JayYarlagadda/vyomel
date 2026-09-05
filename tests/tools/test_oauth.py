"""OAuth least-privilege scopes and refresh rotation (FR-606)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vyomel.core.clock import FrozenClock
from vyomel.core.errors import ErrorCode, ToolError
from vyomel.core.oauth import (
    GMAIL_READONLY,
    GMAIL_SEND,
    FileTokenStore,
    MemoryTokenStore,
    issue_token,
    require_token,
    rotate_refresh,
)


@pytest.mark.req("FR-606")
def test_issue_rejects_scopes_outside_the_least_privilege_set() -> None:
    with pytest.raises(ToolError) as exc:
        issue_token("google", "me", {"https://mail.google.com/"})
    assert exc.value.code is ErrorCode.PERMISSION_DENIED


@pytest.mark.req("FR-606")
def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ToolError) as exc:
        issue_token("slack", "me", set())
    assert exc.value.code is ErrorCode.INVALID_PARAMETERS


@pytest.mark.req("FR-606")
def test_tool_requires_its_declared_scope() -> None:
    store = MemoryTokenStore()
    store.put(issue_token("google", "default", {GMAIL_READONLY}))
    require_token(store, "email.search")
    with pytest.raises(ToolError) as exc:
        require_token(store, "email.send")
    assert exc.value.code is ErrorCode.PERMISSION_DENIED
    assert "gmail.send" in str(exc.value.observation)


@pytest.mark.req("FR-606")
def test_refresh_rotation_invalidates_the_previous_refresh_token() -> None:
    store = MemoryTokenStore()
    original = issue_token("google", "default", {GMAIL_SEND})
    store.put(original)
    rotated = rotate_refresh(store, original)
    assert rotated.access_token != original.access_token
    assert rotated.refresh_token != original.refresh_token
    with pytest.raises(ToolError) as exc:
        rotate_refresh(store, original, presented_refresh=original.refresh_token)
    assert exc.value.code is ErrorCode.PERMISSION_DENIED


@pytest.mark.req("FR-606")
def test_expired_access_token_is_rotated_on_use() -> None:
    store = MemoryTokenStore()
    start = datetime(2026, 9, 3, tzinfo=UTC)
    token = issue_token(
        "google",
        "default",
        {GMAIL_READONLY},
        now=start,
        ttl=timedelta(minutes=1),
    )
    store.put(token)
    later = start + timedelta(minutes=2)
    refreshed = require_token(store, "email.search", now=later)
    assert refreshed.access_token != token.access_token
    assert not refreshed.expired(later)


@pytest.mark.req("FR-606")
def test_file_store_round_trips(tmp_path: Path) -> None:
    store = FileTokenStore(tmp_path / "tokens.json")
    token = issue_token("github", "work", set())
    # empty scopes is allowed (subset of github set)
    store.put(token)
    loaded = store.get("github", "work")
    assert loaded is not None
    assert loaded.account == "work"
    assert store.delete("github", "work") is True
    assert store.get("github", "work") is None


@pytest.mark.req("FR-606")
def test_clock_does_not_need_to_elapse_for_unexpired_tokens() -> None:
    clock = FrozenClock(datetime(2026, 9, 3, tzinfo=UTC))
    store = MemoryTokenStore()
    store.put(issue_token("google", "default", {GMAIL_READONLY}, now=clock.now()))
    again = require_token(store, "email.read", now=clock.now())
    assert GMAIL_READONLY in again.scopes
