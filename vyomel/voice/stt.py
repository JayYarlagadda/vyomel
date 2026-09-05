"""Speech-to-text: fixture backend + optional Whisper-compatible path (FR-1001)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from vyomel.core.errors import ErrorCode, ToolError
from vyomel.core.ids import digest_bytes
from vyomel.voice.types import Transcript, WordTiming

_FIXTURE_MAGIC = b"VYOMEL_VOICE_AUDIO\n"


def is_fixture_audio(data: bytes) -> bool:
    return data.startswith(_FIXTURE_MAGIC)


def encode_fixture_audio(text: str, *, language: str = "en") -> bytes:
    """Embed a deterministic transcript in a pseudo-audio blob for CI."""
    payload = json.dumps({"text": text, "language": language}, ensure_ascii=True)
    return _FIXTURE_MAGIC + payload.encode("utf-8")


def _words_from_text(text: str) -> list[WordTiming]:
    words: list[WordTiming] = []
    t = 0.0
    for token in re.findall(r"\S+", text):
        dur = max(0.2, min(0.6, 0.08 * len(token)))
        words.append(WordTiming(text=token, start=round(t, 3), end=round(t + dur, 3)))
        t += dur + 0.05
    return words


def transcribe_fixture(data: bytes) -> Transcript:
    if not is_fixture_audio(data):
        # Treat UTF-8 text blobs as utterances (handy for unit tests).
        try:
            text = data.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise ToolError(
                "audio is not a Vyomel fixture blob and is not UTF-8 text",
                code=ErrorCode.INVALID_PARAMETERS,
            ) from exc
        if not text:
            raise ToolError("empty audio payload", code=ErrorCode.INVALID_PARAMETERS)
        words = _words_from_text(text)
        return Transcript(
            text=text,
            words=words,
            duration_s=words[-1].end if words else 0.0,
            backend="fixture",
        )
    try:
        payload = json.loads(data[len(_FIXTURE_MAGIC) :].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ToolError(
            "corrupt fixture audio payload",
            code=ErrorCode.INVALID_PARAMETERS,
        ) from exc
    text = str(payload.get("text", "")).strip()
    if not text:
        raise ToolError("fixture audio has empty text", code=ErrorCode.INVALID_PARAMETERS)
    language = str(payload.get("language") or "en")
    words = _words_from_text(text)
    return Transcript(
        text=text,
        language=language,
        words=words,
        duration_s=words[-1].end if words else 0.0,
        backend="fixture",
    )


def transcribe_file(path: Path) -> Transcript:
    if not path.exists() or not path.is_file():
        raise ToolError(
            "audio file does not exist",
            code=ErrorCode.PRECONDITION_FAILED,
            observation=str(path),
        )
    return transcribe_fixture(path.read_bytes())


def transcribe_bytes(data: bytes, *, backend: str = "fixture") -> Transcript:
    if backend in {"fixture", "auto"}:
        return transcribe_fixture(data)
    # Live Whisper path is optional; fall back to fixture decoding when the
    # blob is ours, otherwise refuse closed rather than inventing text.
    if is_fixture_audio(data):
        return transcribe_fixture(data)
    raise ToolError(
        "live Whisper backend is not installed; use fixture audio or VYOMEL_VOICE_BACKEND=fixture",
        code=ErrorCode.UNSUPPORTED,
        detail={"backend": backend, "sha256": digest_bytes(data)[:16]},
    )
