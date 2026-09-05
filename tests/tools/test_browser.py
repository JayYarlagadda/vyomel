"""Browser tools (FR-604)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vyomel.core.cancel import CancellationToken
from vyomel.core.clock import SystemClock
from vyomel.core.config import Settings
from vyomel.core.errors import ErrorCode, ToolError
from vyomel.core.types import Capability
from vyomel.tools.base import ToolContext
from vyomel.tools.browser.metrics import actuation_tier_distribution, reset_actuation_tiers
from vyomel.tools.browser.resolve import parse_dom, resolve_element
from vyomel.tools.browser.session import reset_sessions
from vyomel.tools.browser.types import Target
from vyomel.tools.registry import default_registry


def _ctx(tmp_path: Path) -> ToolContext:
    settings = Settings(
        env="test",
        workspace_root=tmp_path / ".vyomel",
        browser_backend="fixture",
        allowed_roots=[tmp_path],
    )
    settings.ensure_directories()
    return ToolContext(
        task_id="browser-test",
        action_id="a1",
        capability_granted=Capability.L3,
        scratch_dir=settings.scratch_dir,
        allowed_roots=[tmp_path],
        deadline=datetime.now(UTC) + timedelta(seconds=30),
        cancel=CancellationToken(),
        clock=SystemClock(),
        trash_dir=settings.trash_dir,
        settings=settings,
    )


@pytest.mark.req("FR-604")
def test_resolver_prefers_accessibility_over_dom() -> None:
    fixtures = Path(__file__).resolve().parents[2] / "vyomel" / "tools" / "browser" / "fixtures"
    html_text = (fixtures / "job_board_perturbed.html").read_text(encoding="utf-8")
    dom = parse_dom(html_text)
    element, tier = resolve_element(dom, Target(role="button", name="Apply"))
    assert tier == 2
    assert element.name == "Apply"


@pytest.mark.req("FR-604")
async def test_open_query_and_click_fixture_page(tmp_path: Path) -> None:
    reset_sessions()
    reset_actuation_tiers()
    registry = default_registry()
    ctx = _ctx(tmp_path)
    opened = await registry.get("browser.open").execute(
        registry.get("browser.open").Input(url="fixture://job_board"),
        ctx,
    )
    assert opened.title == "Mock Job Board"  # type: ignore[attr-defined]
    queried = await registry.get("browser.query").execute(
        registry.get("browser.query").Input(role="button", name="Apply"),
        ctx,
    )
    assert queried.ref  # type: ignore[attr-defined]
    clicked = await registry.get("browser.click").execute(
        registry.get("browser.click").Input(role="button", name="Apply"),
        ctx,
    )
    assert clicked.clicked is True  # type: ignore[attr-defined]
    assert actuation_tier_distribution()


@pytest.mark.req("FR-604")
async def test_type_rejects_password_without_approval(tmp_path: Path) -> None:
    reset_sessions()
    registry = default_registry()
    ctx = _ctx(tmp_path)
    await registry.get("browser.open").execute(
        registry.get("browser.open").Input(url="fixture://form_app"),
        ctx,
    )
    with pytest.raises(ToolError) as exc:
        await registry.get("browser.type").execute(
            registry.get("browser.type").Input(
                role="textbox",
                name="Password",
                text="secret",
            ),
            ctx,
        )
    assert exc.value.code is ErrorCode.PERMISSION_DENIED
