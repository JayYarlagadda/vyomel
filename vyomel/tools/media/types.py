"""Shared media types (docs/05 §3.6)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SegmentKind = Literal["profanity", "filler", "silence", "highlight"]


class WordTiming(BaseModel):
    text: str
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    tags: list[str] = Field(default_factory=list)


class TimeRange(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)


class MediaProbe(BaseModel):
    path: str
    duration_s: float = Field(ge=0)
    width: int | None = None
    height: int | None = None
    has_audio: bool = True
    has_video: bool = True
    format: str = "fixture"
    sample_rate: int | None = None


class Transcript(BaseModel):
    path: str
    language: str = "en"
    text: str
    words: list[WordTiming]


class DetectedSegment(BaseModel):
    kind: SegmentKind
    start: float
    end: float
    text: str | None = None
    score: float = 1.0


class ClipManifest(BaseModel):
    """On-disk fixture clip descriptor (JSON)."""

    duration_s: float = Field(gt=0)
    width: int = 1280
    height: int = 720
    has_audio: bool = True
    has_video: bool = True
    format: str = "fixture"
    sample_rate: int = 16_000
    words: list[WordTiming] = Field(default_factory=list)
    silence: list[TimeRange] = Field(default_factory=list)
    highlights: list[TimeRange] = Field(default_factory=list)
    # Optional audio/video payload for fixture concat bookkeeping.
    payload: dict[str, Any] = Field(default_factory=dict)
