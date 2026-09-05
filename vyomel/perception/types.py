"""Camera / scene perception types (M17)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class BoundingBox(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(ge=0, le=1)
    h: float = Field(ge=0, le=1)


class Detection(BaseModel):
    label: str
    category: str = "object"
    confidence: float = Field(ge=0, le=1, default=1.0)
    bbox: BoundingBox | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class FrameObservation(BaseModel):
    frame_id: str
    source: str
    width: int = 1280
    height: int = 720
    detections: list[Detection] = Field(default_factory=list)
    backend: str = "fixture"


class TrainingPreference(BaseModel):
    goal: str = "strength"
    days_per_week: int = Field(default=4, ge=1, le=7)
    avoid: list[str] = Field(default_factory=list)
    recent: list[str] = Field(default_factory=list)
    minutes: int = Field(default=45, ge=15, le=120)


class ExerciseBlock(BaseModel):
    name: str
    equipment: str
    sets: int = 3
    reps: str = "8-10"
    notes: str = ""


class GymSessionPlan(BaseModel):
    title: str
    focus: str
    duration_min: int
    blocks: list[ExerciseBlock]
    equipment_used: list[str]
    history_notes: list[str] = Field(default_factory=list)
