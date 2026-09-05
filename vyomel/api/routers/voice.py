"""Voice API (M16): transcribe, speak, session helpers."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from vyomel.core.config import Settings, get_settings
from vyomel.voice.session import VoiceSession
from vyomel.voice.stt import encode_fixture_audio, transcribe_bytes
from vyomel.voice.tts import synthesize
from vyomel.voice.wake import detect_wake, strip_wake

router = APIRouter(prefix="/v1/voice", tags=["voice"])

_SESSIONS: dict[str, VoiceSession] = {}


def _backend(settings: Settings) -> str:
    return "fixture" if settings.voice_backend in {"auto", "fixture"} else settings.voice_backend


def _session(session_id: str, settings: Settings) -> VoiceSession:
    session = _SESSIONS.get(session_id)
    if session is None:
        session = VoiceSession(wake_phrase=settings.voice_wake_phrase, backend=_backend(settings))
        _SESSIONS[session_id] = session
    return session


class TranscribeRequest(BaseModel):
    audio_b64: str | None = None
    text: str | None = Field(
        default=None,
        description="Fixture shortcut: synthesize audio from text",
    )
    strip_wake: bool = False


class TranscribeResponse(BaseModel):
    text: str
    language: str
    duration_s: float
    backend: str
    wake_detected: bool = False
    words: list[dict[str, Any]] = Field(default_factory=list)


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4_000)
    dest: str | None = None


class SpeakResponse(BaseModel):
    path: str
    bytes: int
    duration_s: float
    sha256: str
    format: str


class SessionListenRequest(BaseModel):
    session_id: str = "default"
    audio_b64: str | None = None
    text: str | None = None


class SessionListenResponse(BaseModel):
    wake_detected: bool
    utterance: str
    state: str


class BargeInRequest(BaseModel):
    session_id: str = "default"


class BargeInResponse(BaseModel):
    cancelled: bool
    barge_in_count: int
    state: str


class SessionSpeakRequest(BaseModel):
    session_id: str = "default"
    text: str = Field(min_length=1, max_length=4_000)
    dest: str | None = None
    wait: bool = False


def _audio_from_request(audio_b64: str | None, text: str | None) -> bytes:
    if audio_b64:
        return base64.b64decode(audio_b64)
    if text is not None:
        return encode_fixture_audio(text)
    from vyomel.core.errors import ErrorCode, ToolError

    raise ToolError("audio_b64 or text is required", code=ErrorCode.INVALID_PARAMETERS)


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(
    payload: TranscribeRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> TranscribeResponse:
    data = _audio_from_request(payload.audio_b64, payload.text)
    transcript = transcribe_bytes(data, backend=_backend(settings))
    wake = detect_wake(transcript, phrase=settings.voice_wake_phrase)
    if payload.strip_wake and wake.detected:
        transcript = strip_wake(transcript, phrase=settings.voice_wake_phrase)
    return TranscribeResponse(
        text=transcript.text,
        language=transcript.language,
        duration_s=transcript.duration_s,
        backend=transcript.backend,
        wake_detected=wake.detected,
        words=[w.model_dump() for w in transcript.words],
    )


@router.post("/speak", response_model=SpeakResponse)
async def speak(
    payload: SpeakRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SpeakResponse:
    dest = Path(payload.dest) if payload.dest else settings.scratch_dir / "voice" / "speak.vtts"
    if not payload.dest:
        settings.ensure_directories()
        dest.parent.mkdir(parents=True, exist_ok=True)
    artifact = synthesize(payload.text, dest=dest, backend=_backend(settings))
    return SpeakResponse(
        path=artifact.path,
        bytes=artifact.bytes,
        duration_s=artifact.duration_s,
        sha256=artifact.sha256,
        format=artifact.format,
    )


@router.post("/session/listen", response_model=SessionListenResponse)
async def session_listen(
    payload: SessionListenRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SessionListenResponse:
    session = _session(payload.session_id, settings)
    data = _audio_from_request(payload.audio_b64, payload.text)
    wake = await session.listen_wake(data)
    utterance = ""
    if wake:
        transcript = await session.listen_utterance(data)
        utterance = transcript.text
    return SessionListenResponse(
        wake_detected=wake,
        utterance=utterance,
        state=session.state,
    )


@router.post("/session/speak", response_model=SpeakResponse)
async def session_speak(
    payload: SessionSpeakRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SpeakResponse:
    session = _session(payload.session_id, settings)
    settings.ensure_directories()
    dest = (
        Path(payload.dest)
        if payload.dest
        else settings.scratch_dir / "voice" / f"{payload.session_id}.vtts"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    if payload.wait:
        artifact = await session.speak_and_wait(payload.text, dest=str(dest))
    else:
        artifact = await session.speak(payload.text, dest=str(dest))
    return SpeakResponse(
        path=artifact.path,
        bytes=artifact.bytes,
        duration_s=artifact.duration_s,
        sha256=artifact.sha256,
        format=artifact.format,
    )


@router.post("/session/barge_in", response_model=BargeInResponse)
async def session_barge_in(
    payload: BargeInRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> BargeInResponse:
    session = _session(payload.session_id, settings)
    cancelled = session.barge_in()
    return BargeInResponse(
        cancelled=cancelled,
        barge_in_count=session.barge_in_count,
        state=session.state,
    )


def reset_voice_sessions() -> None:
    _SESSIONS.clear()
