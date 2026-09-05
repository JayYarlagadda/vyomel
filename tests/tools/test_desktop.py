"""Desktop tools (FR-605)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vyomel.core.cancel import CancellationToken
from vyomel.core.clock import SystemClock
from vyomel.core.config import Settings
from vyomel.core.errors import ErrorCode, ToolError
from vyomel.core.types import Capability
from vyomel.tools.base import ToolContext
from vyomel.tools.desktop.metrics import actuation_tier_distribution, reset_actuation_tiers
from vyomel.tools.desktop.session import reset_sessions
from vyomel.tools.registry import default_registry


def _ctx(tmp_path: Path) -> ToolContext:
    settings = Settings(
        env="test",
        workspace_root=tmp_path / ".vyomel",
        desktop_backend="fixture",
        allowed_roots=[tmp_path],
    )
    settings.ensure_directories()
    return ToolContext(
        task_id="desktop-test",
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


@pytest.mark.req("FR-605")
async def test_open_find_click_fixture_app(tmp_path: Path) -> None:
    reset_sessions()
    reset_actuation_tiers()
    registry = default_registry()
    ctx = _ctx(tmp_path)
    opened = await registry.get("app.open").execute(
        registry.get("app.open").Input(target="fixture://gradebook"),
        ctx,
    )
    assert opened.title == "Gradebook"  # type: ignore[attr-defined]
    found = await registry.get("desktop.find_element").execute(
        registry.get("desktop.find_element").Input(role="Button", name="Export CSV"),
        ctx,
    )
    assert found.actuation_tier == 2  # type: ignore[attr-defined]
    clicked = await registry.get("desktop.click_element").execute(
        registry.get("desktop.click_element").Input(role="Button", name="Export CSV"),
        ctx,
    )
    assert clicked.clicked is True  # type: ignore[attr-defined]
    assert actuation_tier_distribution()


@pytest.mark.req("FR-605")
async def test_set_field_and_type_text(tmp_path: Path) -> None:
    reset_sessions()
    registry = default_registry()
    ctx = _ctx(tmp_path)
    await registry.get("app.open").execute(
        registry.get("app.open").Input(target="fixture://student_form"),
        ctx,
    )
    set_result = await registry.get("desktop.set_field").execute(
        registry.get("desktop.set_field").Input(role="Edit", name="Full name", value="Vyomel"),
        ctx,
    )
    assert set_result.value == "Vyomel"  # type: ignore[attr-defined]
    with pytest.raises(ToolError) as exc:
        await registry.get("desktop.type_text").execute(
            registry.get("desktop.type_text").Input(
                role="Edit",
                name="Password",
                text="secret",
            ),
            ctx,
        )
    assert exc.value.code is ErrorCode.PERMISSION_DENIED


def _assert_evidence_file(path: str) -> None:
    assert os.path.isfile(path)
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    assert payload["title"] == "Task Manager"


@pytest.mark.req("FR-605")
async def test_click_xy_captures_evidence(tmp_path: Path) -> None:
    reset_sessions()
    reset_actuation_tiers()
    registry = default_registry()
    ctx = _ctx(tmp_path)
    await registry.get("app.open").execute(
        registry.get("app.open").Input(target="fixture://task_list"),
        ctx,
    )
    result = await registry.get("desktop.click_xy").execute(
        registry.get("desktop.click_xy").Input(x=50, y=314),
        ctx,
    )
    assert result.clicked is True  # type: ignore[attr-defined]
    assert result.actuation_tier == 4  # type: ignore[attr-defined]
    _assert_evidence_file(str(result.evidence_path))  # type: ignore[attr-defined]
