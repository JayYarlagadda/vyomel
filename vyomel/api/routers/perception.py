"""Perception + gym vertical API (M17 / S8)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from vyomel.perception.camera import equipment_labels, observe_frame
from vyomel.perception.gym import build_gym_session, default_history
from vyomel.perception.types import TrainingPreference

router = APIRouter(prefix="/v1/perception", tags=["perception"])


class DetectResponse(BaseModel):
    frame_id: str
    source: str
    backend: str
    equipment: list[str]
    detections: list[dict[str, Any]]


class GymSessionRequest(BaseModel):
    scene: str | None = None
    goal: str = "strength"
    minutes: int = Field(default=45, ge=15, le=120)
    avoid: list[str] = Field(default_factory=lambda: ["heavy axial load"])
    recent: list[str] = Field(default_factory=list)


class GymSessionResponse(BaseModel):
    title: str
    focus: str
    duration_min: int
    equipment_used: list[str]
    blocks: list[dict[str, Any]]
    history_notes: list[str]


@router.get("/detect", response_model=DetectResponse)
async def detect(scene: str | None = None) -> DetectResponse:
    path = Path(scene) if scene else None
    frame = observe_frame(scene_path=path)
    return DetectResponse(
        frame_id=frame.frame_id,
        source=frame.source,
        backend=frame.backend,
        equipment=equipment_labels(frame),
        detections=[d.model_dump() for d in frame.detections],
    )


@router.post("/gym/session", response_model=GymSessionResponse)
async def gym_session(payload: GymSessionRequest) -> GymSessionResponse:
    path = Path(payload.scene) if payload.scene else None
    frame = observe_frame(scene_path=path)
    pref = default_history()
    if payload.goal:
        pref = pref.model_copy(update={"goal": payload.goal})
    if payload.minutes:
        pref = pref.model_copy(update={"minutes": payload.minutes})
    if payload.avoid:
        pref = pref.model_copy(update={"avoid": list(payload.avoid)})
    if payload.recent:
        pref = pref.model_copy(update={"recent": list(payload.recent)})
    # Explicit type for mypy — model_copy returns TrainingPreference.
    preference: TrainingPreference = pref
    plan = build_gym_session(frame, preference=preference)
    return GymSessionResponse(
        title=plan.title,
        focus=plan.focus,
        duration_min=plan.duration_min,
        equipment_used=plan.equipment_used,
        blocks=[b.model_dump() for b in plan.blocks],
        history_notes=plan.history_notes,
    )
