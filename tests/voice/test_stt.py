"""Local Whisper-compatible STT (FR-1001)."""

from __future__ import annotations

import pytest

from vyomel.core.errors import ErrorCode, ToolError
from vyomel.voice.stt import encode_fixture_audio, transcribe_bytes


@pytest.mark.req("FR-1001")
def test_fixture_audio_round_trip() -> None:
    blob = encode_fixture_audio("hey vyomel list my tasks")
    transcript = transcribe_bytes(blob)
    assert "list my tasks" in transcript.text.lower() or "hey vyomel" in transcript.text.lower()
    assert transcript.words
    assert transcript.backend == "fixture"
    assert transcript.duration_s > 0


@pytest.mark.req("FR-1001")
def test_utf8_text_blob_is_accepted() -> None:
    transcript = transcribe_bytes(b"open the dashboard")
    assert transcript.text == "open the dashboard"
    assert len(transcript.words) == 3


@pytest.mark.req("FR-1001")
def test_live_backend_refuses_non_fixture() -> None:
    with pytest.raises(ToolError) as exc:
        transcribe_bytes(b"\x00\x01\x02binary", backend="whisper")
    assert exc.value.code is ErrorCode.UNSUPPORTED
