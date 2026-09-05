"""Optional FFmpeg-backed media operations (live path).

Used when ``VYOMEL_MEDIA_BACKEND=ffmpeg`` and ``ffprobe``/``ffmpeg`` are on PATH.
Transcription still uses fixture word timings when a sibling ``.vclip.json``
exists; otherwise a coarse silence-free stub transcript is produced so the
pipeline stays runnable without Whisper weights in CI.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

from vyomel.core.errors import ErrorCode, ToolError
from vyomel.core.ids import digest_bytes, file_digest
from vyomel.tools.media import fixture as fx
from vyomel.tools.media.types import MediaProbe, Transcript, WordTiming


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _path_exists(path: Path) -> bool:
    return path.exists()


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_text(path: Path, text: str) -> None:
    _ensure_dir(path)
    path.write_text(text, encoding="utf-8")


def _file_size(path: Path) -> int:
    return path.stat().st_size


def _same_path(a: Path, b: Path) -> bool:
    return a.resolve() == b.resolve()


async def _run(argv: list[str]) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out_b, err_b = await proc.communicate()
    return (
        proc.returncode or 0,
        out_b.decode("utf-8", errors="replace"),
        err_b.decode("utf-8", errors="replace"),
    )


async def probe(path: Path) -> MediaProbe:
    if fx.is_fixture_clip(path):
        return fx.probe(path)
    code, stdout, stderr = await _run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )
    if code != 0:
        raise ToolError(
            "ffprobe failed",
            code=ErrorCode.INTERNAL,
            observation=stderr[-500:],
        )
    payload = json.loads(stdout)
    fmt = payload.get("format", {})
    streams = payload.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(fmt.get("duration") or 0.0)
    return MediaProbe(
        path=str(path),
        duration_s=duration,
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        has_audio=audio is not None,
        has_video=video is not None,
        format=str(fmt.get("format_name") or "unknown"),
        sample_rate=int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
    )


async def transcribe(path: Path) -> Transcript:
    sibling = Path(str(path) + ".vclip.json")
    if _path_exists(sibling):
        return fx.transcribe(sibling)
    if fx.is_fixture_clip(path):
        return fx.transcribe(path)
    info = await probe(path)
    word = WordTiming(text="[audio]", start=0.0, end=info.duration_s)
    return Transcript(path=str(path), text=word.text, words=[word])


async def cut(path: Path, *, start: float, end: float, dest: Path) -> MediaProbe:
    if fx.is_fixture_clip(path):
        return fx.cut(path, start=start, end=end, dest=dest)
    _ensure_dir(dest)
    code, _, stderr = await _run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-to",
            f"{end:.3f}",
            "-i",
            str(path),
            "-c",
            "copy",
            str(dest),
        ]
    )
    if code != 0:
        raise ToolError("ffmpeg cut failed", code=ErrorCode.INTERNAL, observation=stderr[-500:])
    return await probe(dest)


async def concat(paths: list[Path], *, dest: Path) -> MediaProbe:
    if all(fx.is_fixture_clip(p) for p in paths):
        return fx.concat(paths, dest=dest)
    _ensure_dir(dest)
    list_file = dest.with_suffix(dest.suffix + ".txt")

    def _write_list() -> None:
        lines = "".join(f"file '{p.resolve().as_posix()}'\n" for p in paths)
        list_file.write_text(lines, encoding="utf-8")

    def _cleanup_list() -> None:
        list_file.unlink(missing_ok=True)

    _write_list()
    code, _, stderr = await _run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            str(dest),
        ]
    )
    _cleanup_list()
    if code != 0:
        raise ToolError(
            "ffmpeg concat failed",
            code=ErrorCode.INTERNAL,
            observation=stderr[-500:],
        )
    return await probe(dest)


async def mute_segment(path: Path, *, start: float, end: float, dest: Path) -> MediaProbe:
    if fx.is_fixture_clip(path):
        return fx.mute_segment(path, start=start, end=end, dest=dest)
    _ensure_dir(dest)
    filt = f"volume=enable='between(t,{start:.3f},{end:.3f})':volume=0"
    code, _, stderr = await _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-af",
            filt,
            "-c:v",
            "copy",
            str(dest),
        ]
    )
    if code != 0:
        raise ToolError(
            "ffmpeg mute failed",
            code=ErrorCode.INTERNAL,
            observation=stderr[-500:],
        )
    return await probe(dest)


async def caption(
    path: Path,
    *,
    dest: Path,
    mode: str,
    transcript: Transcript | None = None,
) -> dict[str, Any]:
    use_fixture = fx.is_fixture_clip(path) or (
        transcript is not None and fx.is_fixture_clip(Path(transcript.path))
    )
    if use_fixture:
        return fx.caption(path, dest=dest, mode=mode, transcript=transcript)
    words = transcript.words if transcript is not None else (await transcribe(path)).words
    srt_path = dest if dest.suffix == ".srt" else dest.with_suffix(".srt")
    _write_text(srt_path, fx.words_to_srt(words))
    media_out = str(path)
    if mode == "burn_in":
        _ensure_dir(dest)
        code, _, stderr = await _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(path),
                "-vf",
                f"subtitles={srt_path.as_posix()}",
                str(dest),
            ]
        )
        if code != 0:
            raise ToolError(
                "ffmpeg caption burn-in failed",
                code=ErrorCode.INTERNAL,
                observation=stderr[-500:],
            )
        media_out = str(dest)
    return {
        "media_path": media_out,
        "srt_path": str(srt_path),
        "mode": mode,
        "cue_count": fx.cue_count(words),
    }


async def export(path: Path, *, dest: Path) -> dict[str, Any]:
    if fx.is_fixture_clip(path):
        return fx.export(path, dest=dest)
    _ensure_dir(dest)
    if not _same_path(path, dest):
        code, _, stderr = await _run(
            ["ffmpeg", "-y", "-i", str(path), "-c", "copy", str(dest)]
        )
        if code != 0:
            raise ToolError(
                "ffmpeg export failed",
                code=ErrorCode.INTERNAL,
                observation=stderr[-500:],
            )
    digest = file_digest(dest)
    return {
        "path": str(dest),
        "bytes": _file_size(dest),
        "sha256": digest,
        "duration_s": (await probe(dest)).duration_s,
    }


def ensure_export_digest(path: Path) -> str:
    return digest_bytes(path.read_bytes()) if _path_exists(path) else ""
