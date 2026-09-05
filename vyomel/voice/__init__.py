"""Voice interface package (M16): STT, wake word, TTS, barge-in."""

from vyomel.voice.session import VoiceSession, fixture_utterance
from vyomel.voice.stt import encode_fixture_audio, transcribe_bytes, transcribe_file
from vyomel.voice.tts import synthesize
from vyomel.voice.types import SpeechArtifact, Transcript, WakeResult
from vyomel.voice.wake import DEFAULT_WAKE_PHRASE, detect_wake, strip_wake

__all__ = [
    "DEFAULT_WAKE_PHRASE",
    "SpeechArtifact",
    "Transcript",
    "VoiceSession",
    "WakeResult",
    "detect_wake",
    "encode_fixture_audio",
    "fixture_utterance",
    "strip_wake",
    "synthesize",
    "transcribe_bytes",
    "transcribe_file",
]
