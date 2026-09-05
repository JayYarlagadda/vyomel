"""Camera tools (docs/05 — perception-backed, FR-1101)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from vyomel.core.types import Capability
from vyomel.perception.camera import equipment_labels, observe_frame
from vyomel.perception.gym import build_gym_session, default_history
from vyomel.perception.types import Detection, TrainingPreference
from vyomel.tools.base import Tool, ToolContext
from vyomel.tools.registry import ToolRegistry


class CameraCaptureInput(BaseModel):
    scene: str | None = Field(
        default=None,
        description="Optional fixture scene path; default gym floor",
    )


class CameraCaptureOutput(BaseModel):
    frame_id: str
    source: str
    width: int
    height: int
    detection_count: int
    backend: str


class CameraDetectInput(BaseModel):
    scene: str | None = None
    category: str | None = Field(default="equipment", description="Filter category or null for all")


class CameraDetectOutput(BaseModel):
    frame_id: str
    detections: list[Detection]
    equipment: list[str]


class GymSessionInput(BaseModel):
    scene: str | None = None
    goal: str = "strength"
    minutes: int = Field(default=45, ge=15, le=120)
    avoid: list[str] = Field(default_factory=lambda: ["heavy axial load"])
    recent: list[str] = Field(
        default_factory=lambda: ["bench press", "overhead press", "cable fly"]
    )


class GymSessionOutput(BaseModel):
    title: str
    focus: str
    duration_min: int
    equipment_used: list[str]
    blocks: list[dict[str, Any]]
    history_notes: list[str]


class CameraCapture(Tool):
    name: ClassVar[str] = "camera.capture"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Capture a camera frame (fixture scene in CI)."
    Input: ClassVar[type[BaseModel]] = CameraCaptureInput
    Output: ClassVar[type[BaseModel]] = CameraCaptureOutput
    base_capability: ClassVar[Capability] = Capability.L0
    reversible: ClassVar[bool] = False
    idempotent: ClassVar[bool] = True
    actuation_tier: ClassVar[int] = 2
    concurrency_key: ClassVar[str] = "camera"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, CameraCaptureInput)
        path = Path(params.scene) if params.scene else None
        frame = observe_frame(scene_path=path)
        return CameraCaptureOutput(
            frame_id=frame.frame_id,
            source=frame.source,
            width=frame.width,
            height=frame.height,
            detection_count=len(frame.detections),
            backend=frame.backend,
        )


class CameraDetect(Tool):
    name: ClassVar[str] = "camera.detect"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Detect objects/equipment in the current or fixture frame."
    Input: ClassVar[type[BaseModel]] = CameraDetectInput
    Output: ClassVar[type[BaseModel]] = CameraDetectOutput
    base_capability: ClassVar[Capability] = Capability.L0
    reversible: ClassVar[bool] = False
    idempotent: ClassVar[bool] = True
    actuation_tier: ClassVar[int] = 2
    concurrency_key: ClassVar[str] = "camera"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, CameraDetectInput)
        path = Path(params.scene) if params.scene else None
        frame = observe_frame(scene_path=path)
        detections = frame.detections
        if params.category:
            detections = [d for d in detections if d.category == params.category]
        return CameraDetectOutput(
            frame_id=frame.frame_id,
            detections=detections,
            equipment=equipment_labels(frame),
        )


class GymPlanSession(Tool):
    name: ClassVar[str] = "gym.plan_session"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Build today's gym session from visible equipment and personal training history (S8)."
    )
    Input: ClassVar[type[BaseModel]] = GymSessionInput
    Output: ClassVar[type[BaseModel]] = GymSessionOutput
    base_capability: ClassVar[Capability] = Capability.L0
    reversible: ClassVar[bool] = False
    idempotent: ClassVar[bool] = True
    concurrency_key: ClassVar[str] = "gym"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, GymSessionInput)
        path = Path(params.scene) if params.scene else None
        frame = observe_frame(scene_path=path)
        pref = TrainingPreference(
            goal=params.goal,
            minutes=params.minutes,
            avoid=list(params.avoid),
            recent=list(params.recent) if params.recent else default_history().recent,
        )
        plan = build_gym_session(frame, preference=pref)
        return GymSessionOutput(
            title=plan.title,
            focus=plan.focus,
            duration_min=plan.duration_min,
            equipment_used=plan.equipment_used,
            blocks=[b.model_dump() for b in plan.blocks],
            history_notes=plan.history_notes,
        )


def register_perception_tools(registry: ToolRegistry) -> None:
    registry.register(CameraCapture())
    registry.register(CameraDetect())
    registry.register(GymPlanSession())
