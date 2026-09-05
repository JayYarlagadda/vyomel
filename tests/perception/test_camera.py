"""Camera perception (FR-1101)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vyomel.core.cancel import CancellationToken
from vyomel.core.clock import SystemClock
from vyomel.core.errors import ErrorCode, ToolError
from vyomel.core.types import Capability
from vyomel.perception.camera import encode_frame, equipment_labels, observe_frame
from vyomel.tools.base import ToolContext
from vyomel.tools.registry import default_registry


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        task_id="cam",
        action_id="a1",
        capability_granted=Capability.L0,
        scratch_dir=tmp_path / "scratch",
        allowed_roots=[tmp_path],
        deadline=datetime.now(UTC) + timedelta(minutes=5),
        cancel=CancellationToken(),
        clock=SystemClock(),
        trash_dir=tmp_path / "trash",
    )


@pytest.mark.req("FR-1101")
def test_default_gym_frame_detects_equipment() -> None:
    frame = observe_frame()
    equipment = equipment_labels(frame)
    assert "barbell" in equipment
    assert "squat_rack" in equipment
    assert "dumbbells" in equipment
    assert frame.backend == "fixture"


@pytest.mark.req("FR-1101")
def test_fixture_file_scene() -> None:
    path = Path("vyomel/perception/fixtures/gym_floor.json")
    frame = observe_frame(scene_path=path)
    assert len(frame.detections) >= 4


@pytest.mark.req("FR-1101")
def test_non_fixture_bytes_refuse_closed() -> None:
    with pytest.raises(ToolError) as exc:
        observe_frame(b"\xff\xd8\xffjpeg")
    assert exc.value.code is ErrorCode.UNSUPPORTED


@pytest.mark.req("FR-1101")
async def test_camera_tools(tmp_path: Path) -> None:
    registry = default_registry()
    ctx = _ctx(tmp_path)
    captured = await registry.get("camera.capture").execute(
        registry.get("camera.capture").Input(),
        ctx,
    )
    assert captured.detection_count >= 1  # type: ignore[attr-defined]
    detected = await registry.get("camera.detect").execute(
        registry.get("camera.detect").Input(category="equipment"),
        ctx,
    )
    assert "bench" in detected.equipment  # type: ignore[attr-defined]


@pytest.mark.req("FR-1101")
def test_encode_frame_round_trip() -> None:
    blob = encode_frame(
        {"source": "t", "detections": [{"label": "bench", "category": "equipment"}]}
    )
    frame = observe_frame(blob)
    assert equipment_labels(frame) == ["bench"]
