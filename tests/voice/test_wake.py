"""Wake-word gating (FR-1002)."""

from __future__ import annotations

import pytest

from vyomel.voice.stt import encode_fixture_audio, transcribe_bytes
from vyomel.voice.wake import detect_wake, strip_wake


@pytest.mark.req("FR-1002")
def test_wake_phrase_detected() -> None:
    transcript = transcribe_bytes(encode_fixture_audio("Hey Vyomel, summarize Orbit"))
    wake = detect_wake(transcript)
    assert wake.detected is True
    cleaned = strip_wake(transcript)
    assert "hey vyomel" not in cleaned.text.lower()
    assert "summarize" in cleaned.text.lower()


@pytest.mark.req("FR-1002")
def test_without_wake_phrase() -> None:
    transcript = transcribe_bytes(encode_fixture_audio("summarize Orbit notes"))
    wake = detect_wake(transcript)
    assert wake.detected is False


@pytest.mark.req("FR-1002")
async def test_session_ignores_pre_wake() -> None:
    from vyomel.voice.session import VoiceSession

    session = VoiceSession()
    assert await session.listen_wake(encode_fixture_audio("just talking")) is False
    assert session.state == "idle"
    assert await session.listen_wake(encode_fixture_audio("hey vyomel do the thing")) is True
    assert session.state == "listening_utterance"
