"""Media backend selection (fixture vs ffmpeg)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from vyomel.core.config import Settings
from vyomel.tools.media import ffmpeg_backend, fixture
from vyomel.tools.media.types import DetectedSegment, MediaProbe, TimeRange, Transcript


class MediaBackend(Protocol):
    async def probe(self, path: Path) -> MediaProbe: ...

    async def transcribe(self, path: Path) -> Transcript: ...

    async def cut(self, path: Path, *, start: float, end: float, dest: Path) -> MediaProbe: ...

    async def concat(self, paths: list[Path], *, dest: Path) -> MediaProbe: ...

    async def mute_segment(
        self, path: Path, *, start: float, end: float, dest: Path
    ) -> MediaProbe: ...

    async def caption(
        self,
        path: Path,
        *,
        dest: Path,
        mode: str,
        transcript: Transcript | None = None,
    ) -> dict[str, Any]: ...

    async def export(self, path: Path, *, dest: Path) -> dict[str, Any]: ...

    def detect_segments(
        self,
        transcript: Transcript,
        *,
        kinds: list[str],
        silence: list[TimeRange] | None = None,
        highlights: list[TimeRange] | None = None,
    ) -> list[DetectedSegment]: ...


class FixtureBackend:
    async def probe(self, path: Path) -> MediaProbe:
        return fixture.probe(path)

    async def transcribe(self, path: Path) -> Transcript:
        return fixture.transcribe(path)

    async def cut(self, path: Path, *, start: float, end: float, dest: Path) -> MediaProbe:
        return fixture.cut(path, start=start, end=end, dest=dest)

    async def concat(self, paths: list[Path], *, dest: Path) -> MediaProbe:
        return fixture.concat(paths, dest=dest)

    async def mute_segment(self, path: Path, *, start: float, end: float, dest: Path) -> MediaProbe:
        return fixture.mute_segment(path, start=start, end=end, dest=dest)

    async def caption(
        self,
        path: Path,
        *,
        dest: Path,
        mode: str,
        transcript: Transcript | None = None,
    ) -> dict[str, Any]:
        return fixture.caption(path, dest=dest, mode=mode, transcript=transcript)

    async def export(self, path: Path, *, dest: Path) -> dict[str, Any]:
        return fixture.export(path, dest=dest)

    def detect_segments(
        self,
        transcript: Transcript,
        *,
        kinds: list[str],
        silence: list[TimeRange] | None = None,
        highlights: list[TimeRange] | None = None,
    ) -> list[DetectedSegment]:
        return fixture.detect_segments(
            transcript, kinds=kinds, silence=silence, highlights=highlights
        )


class FfmpegBackend(FixtureBackend):
    async def probe(self, path: Path) -> MediaProbe:
        return await ffmpeg_backend.probe(path)

    async def transcribe(self, path: Path) -> Transcript:
        return await ffmpeg_backend.transcribe(path)

    async def cut(self, path: Path, *, start: float, end: float, dest: Path) -> MediaProbe:
        return await ffmpeg_backend.cut(path, start=start, end=end, dest=dest)

    async def concat(self, paths: list[Path], *, dest: Path) -> MediaProbe:
        return await ffmpeg_backend.concat(paths, dest=dest)

    async def mute_segment(self, path: Path, *, start: float, end: float, dest: Path) -> MediaProbe:
        return await ffmpeg_backend.mute_segment(path, start=start, end=end, dest=dest)

    async def caption(
        self,
        path: Path,
        *,
        dest: Path,
        mode: str,
        transcript: Transcript | None = None,
    ) -> dict[str, Any]:
        return await ffmpeg_backend.caption(path, dest=dest, mode=mode, transcript=transcript)

    async def export(self, path: Path, *, dest: Path) -> dict[str, Any]:
        return await ffmpeg_backend.export(path, dest=dest)


def backend_name(settings: Settings) -> str:
    mode = settings.media_backend
    if mode == "fixture":
        return "fixture"
    if mode == "ffmpeg":
        return "ffmpeg"
    return "ffmpeg" if ffmpeg_backend.ffmpeg_available() else "fixture"


def get_backend(settings: Settings) -> MediaBackend:
    if backend_name(settings) == "ffmpeg":
        return FfmpegBackend()
    return FixtureBackend()


def fixtures_dir(settings: Settings) -> Path:
    if settings.media_fixtures_dir.is_absolute():
        return settings.media_fixtures_dir
    return Path.cwd() / settings.media_fixtures_dir
