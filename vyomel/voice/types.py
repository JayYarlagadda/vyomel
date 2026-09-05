"""Voice types (M16)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class WordTiming(BaseModel):
    text: str
    start: float = Field(ge=0)
    end: float = Field(ge=0)


class Transcript(BaseModel):
    text: str
    language: str = "en"
    words: list[WordTiming] = Field(default_factory=list)
    duration_s: float = 0.0
    backend: str = "fixture"


class SpeechArtifact(BaseModel):
    path: str
    text: str
    bytes: int
    format: str = "fixture"
    duration_s: float = 0.0
    sha256: str = ""


class WakeResult(BaseModel):
    detected: bool
    phrase: str
    transcript: str
    offset: int = 0


VoiceState = Literal["idle", "listening_wake", "listening_utterance", "speaking", "barge_in"]
