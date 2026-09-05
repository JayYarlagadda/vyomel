"""In-process fixture media backend for deterministic evals and CI.

Clips are JSON manifests (``.vclip.json``). Mutating operations write new
manifests under scratch; ``export`` materializes a content-addressed artifact
so verification can re-observe ``file_exists`` / ``file_hash``.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from vyomel.core.errors import ErrorCode, ToolError
from vyomel.core.ids import digest_bytes
from vyomel.tools.media.types import (
    ClipManifest,
    DetectedSegment,
    MediaProbe,
    TimeRange,
    Transcript,
    WordTiming,
)

_CLIP_SUFFIX = ".vclip.json"


def _as_clip_path(dest: Path) -> Path:
    text = str(dest)
    if text.endswith(_CLIP_SUFFIX):
        return dest
    return Path(text + _CLIP_SUFFIX)


def is_fixture_clip(path: Path) -> bool:
    return path.name.endswith(_CLIP_SUFFIX) or path.suffix == ".json"


def load_manifest(path: Path) -> ClipManifest:
    if not path.exists() or not path.is_file():
        raise ToolError(
            "Media file does not exist",
            code=ErrorCode.PRECONDITION_FAILED,
            observation=str(path),
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolError(
            "Media fixture is not valid JSON",
            code=ErrorCode.INVALID_PARAMETERS,
            observation=str(path),
        ) from exc
    return ClipManifest.model_validate(payload)


def write_manifest(path: Path, manifest: ClipManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")


def probe(path: Path) -> MediaProbe:
    manifest = load_manifest(path)
    return MediaProbe(
        path=str(path),
        duration_s=manifest.duration_s,
        width=manifest.width,
        height=manifest.height,
        has_audio=manifest.has_audio,
        has_video=manifest.has_video,
        format=manifest.format,
        sample_rate=manifest.sample_rate,
    )


def transcribe(path: Path) -> Transcript:
    manifest = load_manifest(path)
    words = list(manifest.words)
    text = " ".join(w.text for w in words)
    return Transcript(path=str(path), text=text, words=words)


def _slice_words(words: list[WordTiming], start: float, end: float) -> list[WordTiming]:
    sliced: list[WordTiming] = []
    for word in words:
        if word.end <= start or word.start >= end:
            continue
        sliced.append(
            WordTiming(
                text=word.text,
                start=max(0.0, word.start - start),
                end=min(end - start, word.end - start),
                tags=list(word.tags),
            )
        )
    return sliced


def _slice_ranges(ranges: list[TimeRange], start: float, end: float) -> list[TimeRange]:
    out: list[TimeRange] = []
    for item in ranges:
        if item.end <= start or item.start >= end:
            continue
        out.append(
            TimeRange(
                start=max(0.0, item.start - start),
                end=min(end - start, item.end - start),
            )
        )
    return out


def cut(path: Path, *, start: float, end: float, dest: Path) -> MediaProbe:
    if end <= start:
        raise ToolError(
            "cut end must be greater than start",
            code=ErrorCode.INVALID_PARAMETERS,
        )
    manifest = load_manifest(path)
    if start < 0 or end > manifest.duration_s + 1e-6:
        raise ToolError(
            "cut range is outside the media duration",
            code=ErrorCode.INVALID_PARAMETERS,
            detail={"duration_s": manifest.duration_s, "start": start, "end": end},
        )
    clipped = ClipManifest(
        duration_s=end - start,
        width=manifest.width,
        height=manifest.height,
        has_audio=manifest.has_audio,
        has_video=manifest.has_video,
        format=manifest.format,
        sample_rate=manifest.sample_rate,
        words=_slice_words(manifest.words, start, end),
        silence=_slice_ranges(manifest.silence, start, end),
        highlights=_slice_ranges(manifest.highlights, start, end),
        payload={"source": str(path), "cut": [start, end]},
    )
    dest = _as_clip_path(dest)
    write_manifest(dest, clipped)
    return probe(dest)


def concat(paths: list[Path], *, dest: Path) -> MediaProbe:
    if not paths:
        raise ToolError("concat requires at least one input", code=ErrorCode.INVALID_PARAMETERS)
    parts = [load_manifest(p) for p in paths]
    words: list[WordTiming] = []
    silence: list[TimeRange] = []
    highlights: list[TimeRange] = []
    offset = 0.0
    for part in parts:
        for word in part.words:
            words.append(
                WordTiming(
                    text=word.text,
                    start=word.start + offset,
                    end=word.end + offset,
                    tags=list(word.tags),
                )
            )
        for item in part.silence:
            silence.append(TimeRange(start=item.start + offset, end=item.end + offset))
        for item in part.highlights:
            highlights.append(TimeRange(start=item.start + offset, end=item.end + offset))
        offset += part.duration_s
    first = parts[0]
    merged = ClipManifest(
        duration_s=offset,
        width=first.width,
        height=first.height,
        has_audio=any(p.has_audio for p in parts),
        has_video=any(p.has_video for p in parts),
        format=first.format,
        sample_rate=first.sample_rate,
        words=words,
        silence=silence,
        highlights=highlights,
        payload={"sources": [str(p) for p in paths]},
    )
    dest = _as_clip_path(dest)
    write_manifest(dest, merged)
    return probe(dest)


def mute_segment(path: Path, *, start: float, end: float, dest: Path) -> MediaProbe:
    if end <= start:
        raise ToolError(
            "mute end must be greater than start",
            code=ErrorCode.INVALID_PARAMETERS,
        )
    manifest = load_manifest(path)
    muted_words: list[WordTiming] = []
    for word in manifest.words:
        if word.start >= start and word.end <= end:
            muted_words.append(
                WordTiming(text="[muted]", start=word.start, end=word.end, tags=["muted"])
            )
        else:
            muted_words.append(word)
    silence = [*manifest.silence, TimeRange(start=start, end=end)]
    updated = manifest.model_copy(
        update={
            "words": muted_words,
            "silence": silence,
            "payload": {**manifest.payload, "muted": [start, end]},
        }
    )
    dest = _as_clip_path(dest)
    write_manifest(dest, updated)
    return probe(dest)


def caption(
    path: Path,
    *,
    dest: Path,
    mode: str,
    transcript: Transcript | None = None,
) -> dict[str, Any]:
    manifest = load_manifest(path)
    words = transcript.words if transcript is not None else manifest.words
    srt_path = dest if dest.suffix == ".srt" else dest.with_suffix(".srt")
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text(words_to_srt(words), encoding="utf-8")
    out_media = dest if mode == "burn_in" else path
    if mode == "burn_in":
        out_media = _as_clip_path(Path(out_media))
        burned = manifest.model_copy(
            update={"payload": {**manifest.payload, "captions": str(srt_path)}}
        )
        write_manifest(out_media, burned)
    return {
        "media_path": str(out_media if mode == "burn_in" else path),
        "srt_path": str(srt_path),
        "mode": mode,
        "cue_count": cue_count(words),
    }


def export(path: Path, *, dest: Path) -> dict[str, Any]:
    manifest = load_manifest(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Materialize a deterministic binary stand-in so file_hash verification works.
    body = (
        b"VYOMEL_FIXTURE_EXPORT\n"
        + manifest.model_dump_json().encode("utf-8")
        + b"\n"
    )
    digest = digest_bytes(body)
    dest.write_bytes(body)
    return {
        "path": str(dest),
        "bytes": len(body),
        "sha256": digest,
        "duration_s": manifest.duration_s,
    }


_FILLERS = frozenset({"um", "uh", "erm", "ah", "like", "you know"})
_PROFANITY = frozenset({"damn", "hell", "crap", "shit", "ass", "bastard"})


def detect_segments(
    transcript: Transcript,
    *,
    kinds: list[str],
    silence: list[TimeRange] | None = None,
    highlights: list[TimeRange] | None = None,
) -> list[DetectedSegment]:
    wanted = set(kinds)
    found: list[DetectedSegment] = []
    if "profanity" in wanted:
        for word in transcript.words:
            token = re.sub(r"[^a-z]", "", word.text.lower())
            if token in _PROFANITY or "profanity" in word.tags:
                found.append(
                    DetectedSegment(
                        kind="profanity",
                        start=word.start,
                        end=word.end,
                        text=word.text,
                        score=1.0,
                    )
                )
    if "filler" in wanted:
        for word in transcript.words:
            token = re.sub(r"[^a-z]", "", word.text.lower())
            if token in _FILLERS or "filler" in word.tags:
                found.append(
                    DetectedSegment(
                        kind="filler",
                        start=word.start,
                        end=word.end,
                        text=word.text,
                        score=0.9,
                    )
                )
    if "silence" in wanted:
        for item in silence or []:
            found.append(
                DetectedSegment(kind="silence", start=item.start, end=item.end, score=1.0)
            )
    if "highlight" in wanted:
        for item in highlights or []:
            found.append(
                DetectedSegment(kind="highlight", start=item.start, end=item.end, score=0.8)
            )
    found.sort(key=lambda s: (s.start, s.end, s.kind))
    return found


def _format_ts(seconds: float) -> str:
    ms = round(seconds * 1000)
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def words_to_srt(words: list[WordTiming]) -> str:
    if not words:
        return ""
    cues: list[tuple[float, float, str]] = []
    bucket: list[WordTiming] = []
    for word in words:
        if word.text == "[muted]":
            if bucket:
                cues.append((bucket[0].start, bucket[-1].end, " ".join(w.text for w in bucket)))
                bucket = []
            continue
        bucket.append(word)
        if len(bucket) >= 8 or (bucket and word.end - bucket[0].start >= 3.0):
            cues.append((bucket[0].start, bucket[-1].end, " ".join(w.text for w in bucket)))
            bucket = []
    if bucket:
        cues.append((bucket[0].start, bucket[-1].end, " ".join(w.text for w in bucket)))
    lines: list[str] = []
    for idx, (start, end, text) in enumerate(cues, start=1):
        lines.append(str(idx))
        lines.append(f"{_format_ts(start)} --> {_format_ts(end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def cue_count(words: list[WordTiming]) -> int:
    return len([line for line in words_to_srt(words).splitlines() if line.isdigit()])


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(65_536)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()
