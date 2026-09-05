"""Media tools (docs/05 §3.6, FR-607)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from vyomel.core.config import Settings, get_settings
from vyomel.core.errors import ErrorCode, ToolError
from vyomel.core.types import Capability
from vyomel.tools.base import Tool, ToolContext
from vyomel.tools.media import fixture as fx
from vyomel.tools.media.session import get_backend
from vyomel.tools.media.types import DetectedSegment, SegmentKind, Transcript, WordTiming
from vyomel.tools.registry import ToolRegistry
from vyomel.tools.sandbox import resolve_in_sandbox


def _settings(ctx: ToolContext) -> Settings:
    return ctx.settings or get_settings()


def _resolve_src(path: str, ctx: ToolContext) -> Path:
    return resolve_in_sandbox(path, ctx.allowed_roots)


def _resolve_dest(path: str, ctx: ToolContext, *, scratch_only: bool = True) -> Path:
    dest = resolve_in_sandbox(path, ctx.allowed_roots)
    if scratch_only:
        scratch = ctx.scratch_dir.resolve(strict=False)
        if not (dest == scratch or dest.is_relative_to(scratch)):
            raise ToolError(
                "Intermediate media writes must stay inside scratch",
                code=ErrorCode.PERMISSION_DENIED,
                observation=str(dest),
            )
    return dest


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _unlink_if_exists(path: Path) -> None:
    if path.exists():
        path.unlink()


def _unlink_outputs(paths: list[Path], *, keep: Path) -> None:
    keep_resolved = keep.resolve()
    for path in paths:
        if path.exists() and path.resolve() != keep_resolved:
            path.unlink()


# --- probe ---


class ProbeInput(BaseModel):
    path: str = Field(min_length=1)


class ProbeOutput(BaseModel):
    path: str
    duration_s: float
    width: int | None = None
    height: int | None = None
    has_audio: bool
    has_video: bool
    format: str


class MediaProbeTool(Tool):
    name: ClassVar[str] = "media.probe"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Probe a media file for duration, streams, and container format."
    Input: ClassVar[type[BaseModel]] = ProbeInput
    Output: ClassVar[type[BaseModel]] = ProbeOutput
    base_capability: ClassVar[Capability] = Capability.L0
    reversible: ClassVar[bool] = False
    idempotent: ClassVar[bool] = True
    concurrency_key: ClassVar[str] = "media"
    default_timeout_s: ClassVar[int] = 60

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, ProbeInput)
        path = _resolve_src(params.path, ctx)
        info = await get_backend(_settings(ctx)).probe(path)
        return ProbeOutput(
            path=info.path,
            duration_s=info.duration_s,
            width=info.width,
            height=info.height,
            has_audio=info.has_audio,
            has_video=info.has_video,
            format=info.format,
        )


# --- transcribe ---


class TranscribeInput(BaseModel):
    path: str = Field(min_length=1)
    language: str = Field(default="en", min_length=2, max_length=16)


class TranscribeOutput(BaseModel):
    path: str
    language: str
    text: str
    words: list[WordTiming]


class MediaTranscribe(Tool):
    name: ClassVar[str] = "media.transcribe"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Transcribe media with word-level timestamps (local Whisper / fixture backend)."
    )
    Input: ClassVar[type[BaseModel]] = TranscribeInput
    Output: ClassVar[type[BaseModel]] = TranscribeOutput
    base_capability: ClassVar[Capability] = Capability.L0
    reversible: ClassVar[bool] = False
    idempotent: ClassVar[bool] = True
    concurrency_key: ClassVar[str] = "media"
    default_timeout_s: ClassVar[int] = 300

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, TranscribeInput)
        path = _resolve_src(params.path, ctx)
        transcript = await get_backend(_settings(ctx)).transcribe(path)
        return TranscribeOutput(
            path=transcript.path,
            language=params.language,
            text=transcript.text,
            words=transcript.words,
        )


# --- detect_segments ---


_DEFAULT_SEGMENT_KINDS: tuple[SegmentKind, ...] = (
    "profanity",
    "filler",
    "silence",
    "highlight",
)


class DetectSegmentsInput(BaseModel):
    path: str = Field(min_length=1)
    kinds: list[SegmentKind] = Field(default_factory=lambda: list(_DEFAULT_SEGMENT_KINDS))
    transcript_text: str | None = None
    words: list[WordTiming] | None = None


class DetectSegmentsOutput(BaseModel):
    path: str
    segments: list[DetectedSegment]


class MediaDetectSegments(Tool):
    name: ClassVar[str] = "media.detect_segments"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Detect profanity, filler, silence, and highlight segments over a transcript."
    )
    Input: ClassVar[type[BaseModel]] = DetectSegmentsInput
    Output: ClassVar[type[BaseModel]] = DetectSegmentsOutput
    base_capability: ClassVar[Capability] = Capability.L0
    reversible: ClassVar[bool] = False
    idempotent: ClassVar[bool] = True
    concurrency_key: ClassVar[str] = "media"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, DetectSegmentsInput)
        path = _resolve_src(params.path, ctx)
        backend = get_backend(_settings(ctx))
        if params.words is not None:
            transcript = Transcript(
                path=str(path),
                text=params.transcript_text or " ".join(w.text for w in params.words),
                words=params.words,
            )
        else:
            transcript = await backend.transcribe(path)
        silence = highlights = None
        if fx.is_fixture_clip(path):
            manifest = fx.load_manifest(path)
            silence = manifest.silence
            highlights = manifest.highlights
        segments = backend.detect_segments(
            transcript,
            kinds=list(params.kinds),
            silence=silence,
            highlights=highlights,
        )
        return DetectSegmentsOutput(path=str(path), segments=segments)


# --- cut ---


class CutInput(BaseModel):
    path: str = Field(min_length=1)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    dest: str = Field(min_length=1)


class CutOutput(BaseModel):
    path: str
    duration_s: float
    dest: str


class MediaCut(Tool):
    name: ClassVar[str] = "media.cut"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Cut a time range from media into a scratch output."
    Input: ClassVar[type[BaseModel]] = CutInput
    Output: ClassVar[type[BaseModel]] = CutOutput
    base_capability: ClassVar[Capability] = Capability.L1
    reversible: ClassVar[bool] = True
    idempotent: ClassVar[bool] = True
    concurrency_key: ClassVar[str] = "media"
    default_timeout_s: ClassVar[int] = 120

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, CutInput)
        src = _resolve_src(params.path, ctx)
        dest = _resolve_dest(params.dest, ctx)
        _ensure_parent(dest)
        info = await get_backend(_settings(ctx)).cut(
            src, start=params.start, end=params.end, dest=dest
        )
        return CutOutput(path=info.path, duration_s=info.duration_s, dest=info.path)

    async def compensate(self, params: BaseModel, result: BaseModel, ctx: ToolContext) -> None:
        assert isinstance(result, CutOutput)
        _unlink_if_exists(Path(result.dest))


# --- concat ---


class ConcatInput(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=64)
    dest: str = Field(min_length=1)


class ConcatOutput(BaseModel):
    path: str
    duration_s: float
    dest: str
    source_count: int


class MediaConcat(Tool):
    name: ClassVar[str] = "media.concat"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Concatenate media clips into a scratch output."
    Input: ClassVar[type[BaseModel]] = ConcatInput
    Output: ClassVar[type[BaseModel]] = ConcatOutput
    base_capability: ClassVar[Capability] = Capability.L1
    reversible: ClassVar[bool] = True
    idempotent: ClassVar[bool] = True
    concurrency_key: ClassVar[str] = "media"
    default_timeout_s: ClassVar[int] = 180

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, ConcatInput)
        sources = [_resolve_src(p, ctx) for p in params.paths]
        dest = _resolve_dest(params.dest, ctx)
        _ensure_parent(dest)
        info = await get_backend(_settings(ctx)).concat(sources, dest=dest)
        return ConcatOutput(
            path=info.path,
            duration_s=info.duration_s,
            dest=info.path,
            source_count=len(sources),
        )

    async def compensate(self, params: BaseModel, result: BaseModel, ctx: ToolContext) -> None:
        assert isinstance(result, ConcatOutput)
        _unlink_if_exists(Path(result.dest))


# --- mute_segment ---


class MuteInput(BaseModel):
    path: str = Field(min_length=1)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    dest: str = Field(min_length=1)


class MuteOutput(BaseModel):
    path: str
    duration_s: float
    dest: str
    muted_start: float
    muted_end: float


class MediaMuteSegment(Tool):
    name: ClassVar[str] = "media.mute_segment"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Mute audio between two timestamps into a scratch output."
    Input: ClassVar[type[BaseModel]] = MuteInput
    Output: ClassVar[type[BaseModel]] = MuteOutput
    base_capability: ClassVar[Capability] = Capability.L1
    reversible: ClassVar[bool] = True
    idempotent: ClassVar[bool] = True
    concurrency_key: ClassVar[str] = "media"
    default_timeout_s: ClassVar[int] = 120

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, MuteInput)
        src = _resolve_src(params.path, ctx)
        dest = _resolve_dest(params.dest, ctx)
        _ensure_parent(dest)
        info = await get_backend(_settings(ctx)).mute_segment(
            src, start=params.start, end=params.end, dest=dest
        )
        return MuteOutput(
            path=info.path,
            duration_s=info.duration_s,
            dest=info.path,
            muted_start=params.start,
            muted_end=params.end,
        )

    async def compensate(self, params: BaseModel, result: BaseModel, ctx: ToolContext) -> None:
        assert isinstance(result, MuteOutput)
        _unlink_if_exists(Path(result.dest))


# --- caption ---


class CaptionInput(BaseModel):
    path: str = Field(min_length=1)
    dest: str = Field(min_length=1)
    mode: Literal["sidecar", "burn_in"] = "sidecar"
    words: list[WordTiming] | None = None


class CaptionOutput(BaseModel):
    media_path: str
    srt_path: str
    mode: str
    cue_count: int


class MediaCaption(Tool):
    name: ClassVar[str] = "media.caption"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Write captions as a sidecar .srt or burn them into the video."
    Input: ClassVar[type[BaseModel]] = CaptionInput
    Output: ClassVar[type[BaseModel]] = CaptionOutput
    base_capability: ClassVar[Capability] = Capability.L1
    reversible: ClassVar[bool] = True
    idempotent: ClassVar[bool] = True
    concurrency_key: ClassVar[str] = "media"
    default_timeout_s: ClassVar[int] = 180

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, CaptionInput)
        src = _resolve_src(params.path, ctx)
        dest = _resolve_dest(params.dest, ctx)
        _ensure_parent(dest)
        transcript = None
        if params.words is not None:
            transcript = Transcript(
                path=str(src),
                text=" ".join(w.text for w in params.words),
                words=params.words,
            )
        result = await get_backend(_settings(ctx)).caption(
            src, dest=dest, mode=params.mode, transcript=transcript
        )
        return CaptionOutput(
            media_path=result["media_path"],
            srt_path=result["srt_path"],
            mode=result["mode"],
            cue_count=int(result["cue_count"]),
        )

    async def compensate(self, params: BaseModel, result: BaseModel, ctx: ToolContext) -> None:
        assert isinstance(params, CaptionInput)
        assert isinstance(result, CaptionOutput)
        _unlink_outputs(
            [Path(result.srt_path), Path(result.media_path)],
            keep=_resolve_src(params.path, ctx),
        )


# --- export ---


class ExportInput(BaseModel):
    path: str = Field(min_length=1)
    dest: str = Field(min_length=1)


class ExportOutput(BaseModel):
    path: str
    bytes: int
    sha256: str
    duration_s: float


class MediaExport(Tool):
    name: ClassVar[str] = "media.export"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Export the final media artifact to an allowlisted destination."
    Input: ClassVar[type[BaseModel]] = ExportInput
    Output: ClassVar[type[BaseModel]] = ExportOutput
    base_capability: ClassVar[Capability] = Capability.L2
    reversible: ClassVar[bool] = True
    idempotent: ClassVar[bool] = True
    concurrency_key: ClassVar[str] = "media"
    default_timeout_s: ClassVar[int] = 180

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        assert isinstance(result, ExportOutput)
        return [
            {"type": "file_exists", "path": result.path, "tier": 1},
            {"type": "file_hash", "path": result.path, "expected": result.sha256, "tier": 1},
        ]

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, ExportInput)
        src = _resolve_src(params.path, ctx)
        # Final artifact may leave scratch (still sandboxed).
        dest = _resolve_dest(params.dest, ctx, scratch_only=False)
        _ensure_parent(dest)
        result = await get_backend(_settings(ctx)).export(src, dest=dest)
        return ExportOutput(
            path=result["path"],
            bytes=int(result["bytes"]),
            sha256=str(result["sha256"]),
            duration_s=float(result["duration_s"]),
        )

    async def compensate(self, params: BaseModel, result: BaseModel, ctx: ToolContext) -> None:
        assert isinstance(result, ExportOutput)
        _unlink_if_exists(Path(result.path))


def register_media_tools(registry: ToolRegistry) -> None:
    registry.register(MediaProbeTool())
    registry.register(MediaTranscribe())
    registry.register(MediaDetectSegments())
    registry.register(MediaCut())
    registry.register(MediaConcat())
    registry.register(MediaMuteSegment())
    registry.register(MediaCaption())
    registry.register(MediaExport())
