"""Barge-in cancels in-progress TTS (FR-1004)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vyomel.voice.session import VoiceSession
from vyomel.voice.tts import synthesize


@pytest.mark.req("FR-1004")
async def test_barge_in_cancels_speech(tmp_path: Path) -> None:
    session = VoiceSession()
    # Force a long playback window by synthesizing then overriding duration via speak sleep.
    # Speak uses artifact.duration_s; craft a long text so sleep is noticeable.
    long_text = " ".join(["word"] * 80)
    dest = tmp_path / "long.vtts"
    artifact = await session.speak(long_text, dest=str(dest))
    assert artifact.duration_s >= 1.0
    assert session.state == "speaking"
    await asyncio.sleep(0.05)
    assert session.barge_in() is True
    assert session.barge_in_count == 1
    assert session.state == "barge_in"
    await session.wait_speech_done()
    # Second barge_in with nothing playing is a no-op.
    assert session.barge_in() is False


@pytest.mark.req("FR-1004")
async def test_new_speak_preempts_previous(tmp_path: Path) -> None:
    session = VoiceSession()
    first = await session.speak("first utterance " * 20, dest=str(tmp_path / "a.vtts"))
    assert first.duration_s > 0.5
    second = await session.speak("second", dest=str(tmp_path / "b.vtts"))
    assert session.barge_in_count >= 1
    assert second.text == "second"
    await session.wait_speech_done()


@pytest.mark.req("FR-1004")
def test_synthesize_helper_used_by_session(tmp_path: Path) -> None:
    # Sanity: barge-in path depends on synthesize producing positive duration.
    art = synthesize("one two three four five six", dest=tmp_path / "t.vtts")
    assert art.duration_s > 0
