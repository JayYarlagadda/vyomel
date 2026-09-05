"""Voice session: wake → listen → speak with barge-in (FR-1002, FR-1004)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from vyomel.core.errors import ErrorCode, ToolError
from vyomel.voice.stt import encode_fixture_audio, transcribe_bytes
from vyomel.voice.tts import synthesize
from vyomel.voice.types import SpeechArtifact, Transcript, VoiceState
from vyomel.voice.wake import DEFAULT_WAKE_PHRASE, detect_wake, strip_wake


@dataclass
class VoiceSession:
    """In-process duplex voice session.

    TTS is simulated with an asyncio Task that sleeps for the artifact
    duration; ``barge_in()`` cancels it immediately (FR-1004).
    """

    wake_phrase: str = DEFAULT_WAKE_PHRASE
    backend: str = "fixture"
    state: VoiceState = "idle"
    last_transcript: Transcript | None = None
    last_speech: SpeechArtifact | None = None
    barge_in_count: int = 0
    _speak_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _barge_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def reset(self) -> None:
        self._cancel_speak()
        self.state = "idle"
        self.last_transcript = None
        self.last_speech = None
        self.barge_in_count = 0
        self._barge_event = asyncio.Event()

    def _cancel_speak(self) -> bool:
        task = self._speak_task
        if task is None or task.done():
            self._speak_task = None
            return False
        task.cancel()
        self._speak_task = None
        self._barge_event.set()
        return True

    def barge_in(self) -> bool:
        """Interrupt in-progress TTS. Returns True if something was cancelled."""
        cancelled = self._cancel_speak()
        if cancelled:
            self.barge_in_count += 1
            self.state = "barge_in"
        return cancelled

    async def listen_wake(self, audio: bytes) -> bool:
        self.state = "listening_wake"
        transcript = transcribe_bytes(audio, backend=self.backend)
        self.last_transcript = transcript
        wake = detect_wake(transcript, phrase=self.wake_phrase)
        if wake.detected:
            self.state = "listening_utterance"
        else:
            self.state = "idle"
        return wake.detected

    async def listen_utterance(self, audio: bytes, *, strip_wake_phrase: bool = True) -> Transcript:
        self.state = "listening_utterance"
        transcript = transcribe_bytes(audio, backend=self.backend)
        if strip_wake_phrase:
            transcript = strip_wake(transcript, phrase=self.wake_phrase)
        if not transcript.text.strip():
            raise ToolError("empty utterance after wake strip", code=ErrorCode.INVALID_PARAMETERS)
        self.last_transcript = transcript
        self.state = "idle"
        return transcript

    async def speak(self, text: str, *, dest: str, allow_barge_in: bool = True) -> SpeechArtifact:
        # New speech cancels any prior playback.
        self.barge_in()
        self._barge_event = asyncio.Event()
        artifact = synthesize(text, dest=Path(dest), backend=self.backend)
        self.last_speech = artifact
        self.state = "speaking"

        async def _play() -> None:
            try:
                await asyncio.sleep(artifact.duration_s)
            except asyncio.CancelledError:
                raise
            finally:
                if self.state == "speaking":
                    self.state = "idle"

        self._speak_task = asyncio.create_task(_play())
        if not allow_barge_in:
            await self._speak_task
            return artifact

        # Return immediately; caller may barge_in while playback runs.
        # For tests that need completion, await wait_speech_done().
        return artifact

    async def wait_speech_done(self) -> None:
        task = self._speak_task
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            return

    async def speak_and_wait(self, text: str, *, dest: str) -> SpeechArtifact:
        artifact = await self.speak(text, dest=dest, allow_barge_in=True)
        await self.wait_speech_done()
        return artifact


def fixture_utterance(text: str) -> bytes:
    return encode_fixture_audio(text)
