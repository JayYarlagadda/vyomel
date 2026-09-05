"""Text-to-speech fixture backend (FR-1003)."""

from __future__ import annotations

import json
from pathlib import Path

from vyomel.core.errors import ErrorCode, ToolError
from vyomel.core.ids import digest_bytes
from vyomel.voice.types import SpeechArtifact

_TTS_MAGIC = b"VYOMEL_VOICE_TTS\n"


def synthesize(
    text: str,
    *,
    dest: Path,
    backend: str = "fixture",
) -> SpeechArtifact:
    cleaned = text.strip()
    if not cleaned:
        raise ToolError("TTS text is empty", code=ErrorCode.INVALID_PARAMETERS)
    if backend not in {"fixture", "auto"}:
        # No live engine wired in default installs; refuse closed.
        raise ToolError(
            "live TTS backend is not installed; use VYOMEL_VOICE_BACKEND=fixture",
            code=ErrorCode.UNSUPPORTED,
            detail={"backend": backend},
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    duration_s = max(0.4, 0.06 * len(cleaned.split()))
    payload = {
        "text": cleaned,
        "format": "fixture",
        "approx_duration_s": duration_s,
    }
    body = _TTS_MAGIC + json.dumps(payload, ensure_ascii=True).encode("utf-8")
    dest.write_bytes(body)
    return SpeechArtifact(
        path=str(dest),
        text=cleaned,
        bytes=len(body),
        format="fixture",
        duration_s=duration_s,
        sha256=digest_bytes(body),
    )


def read_tts_text(path: Path) -> str:
    data = path.read_bytes()
    if not data.startswith(_TTS_MAGIC):
        raise ToolError("not a fixture TTS artifact", code=ErrorCode.INVALID_PARAMETERS)
    payload = json.loads(data[len(_TTS_MAGIC) :].decode("utf-8"))
    return str(payload["text"])
