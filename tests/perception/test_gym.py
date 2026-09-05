"""Gym scenario S8 (FR-1102)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vyomel.core.cancel import CancellationToken
from vyomel.core.clock import SystemClock
from vyomel.core.types import Capability
from vyomel.perception.gym import build_gym_session, default_history
from vyomel.perception.types import TrainingPreference
from vyomel.tools.base import ToolContext
from vyomel.tools.registry import default_registry


@pytest.mark.req("FR-1102")
def test_s8_builds_session_from_equipment_and_history() -> None:
    pref = default_history()
    plan = build_gym_session(preference=pref)
    assert plan.blocks
    assert plan.equipment_used
    assert plan.duration_min == pref.minutes
    assert plan.focus == "pull_and_legs"  # recent was push work
    # Axial squat should be skipped when avoid includes heavy axial load.
    names = " ".join(b.name.lower() for b in plan.blocks)
    assert "back squat" not in names


@pytest.mark.req("FR-1102")
def test_push_focus_when_recent_was_pull() -> None:
    pref = TrainingPreference(
        recent=["barbell row", "pull-ups", "rdl"],
        avoid=[],
        minutes=40,
    )
    plan = build_gym_session(preference=pref)
    assert plan.focus == "push"
    assert plan.blocks


@pytest.mark.req("FR-1102")
async def test_gym_plan_session_tool(tmp_path: Path) -> None:
    registry = default_registry()
    ctx = ToolContext(
        task_id="gym",
        action_id="a1",
        capability_granted=Capability.L0,
        scratch_dir=tmp_path / "scratch",
        allowed_roots=[tmp_path],
        deadline=datetime.now(UTC) + timedelta(minutes=5),
        cancel=CancellationToken(),
        clock=SystemClock(),
        trash_dir=tmp_path / "trash",
    )
    result = await registry.get("gym.plan_session").execute(
        registry.get("gym.plan_session").Input(),
        ctx,
    )
    assert result.blocks  # type: ignore[attr-defined]
    assert result.equipment_used  # type: ignore[attr-defined]
