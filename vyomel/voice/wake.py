"""Wake-word gating (FR-1002)."""

from __future__ import annotations

import re

from vyomel.voice.types import Transcript, WakeResult

DEFAULT_WAKE_PHRASE = "hey vyomel"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def detect_wake(
    transcript: Transcript | str,
    *,
    phrase: str = DEFAULT_WAKE_PHRASE,
) -> WakeResult:
    """Return whether ``phrase`` appears as a contiguous word sequence."""
    text = transcript if isinstance(transcript, str) else transcript.text
    norm = _normalize(text)
    target = _normalize(phrase)
    if not target:
        return WakeResult(detected=False, phrase=phrase, transcript=text)
    offset = norm.find(target)
    if offset < 0:
        # Allow punctuation between words: "hey, vyomel"
        loose = re.sub(r"[^\w\s]", " ", norm)
        loose = _normalize(loose)
        offset = loose.find(target)
        if offset < 0:
            return WakeResult(detected=False, phrase=phrase, transcript=text)
        norm = loose
    return WakeResult(detected=True, phrase=phrase, transcript=text, offset=offset)


def strip_wake(transcript: Transcript, *, phrase: str = DEFAULT_WAKE_PHRASE) -> Transcript:
    """Remove a leading wake phrase from the transcript text."""
    wake = detect_wake(transcript, phrase=phrase)
    if not wake.detected:
        return transcript
    pattern = re.compile(re.escape(phrase), re.IGNORECASE)
    cleaned = pattern.sub("", transcript.text, count=1).strip(" ,.-")
    words = [w for w in transcript.words if _normalize(w.text) not in _normalize(phrase).split()]
    return Transcript(
        text=cleaned,
        language=transcript.language,
        words=words,
        duration_s=transcript.duration_s,
        backend=transcript.backend,
    )
