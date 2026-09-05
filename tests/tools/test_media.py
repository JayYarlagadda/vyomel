"""Media tools (FR-607)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vyomel.core.cancel import CancellationToken
from vyomel.core.clock import FrozenClock
from vyomel.core.config import Settings
from vyomel.core.errors import ErrorCode, ToolError
from vyomel.core.types import Capability
from vyomel.security.policy import PolicyRequest, load_policy, variables_for
from vyomel.tools.base import ToolContext
from vyomel.tools.media.session import fixtures_dir
from vyomel.tools.registry import default_registry

NOW = datetime(2026, 9, 4, 16, 0, tzinfo=UTC)


def _settings(tmp_path: Path) -> Settings:
    settings = Settings(
        env="test",
        workspace_root=tmp_path / ".vyomel",
        allowed_roots=[tmp_path, tmp_path / ".vyomel"],
        media_backend="fixture",
        media_fixtures_dir=Path("vyomel/tools/media/fixtures"),
    )
    settings.ensure_directories()
    return settings


def _ctx(tmp_path: Path) -> ToolContext:
    settings = _settings(tmp_path)
    return ToolContext(
        task_id="media-test",
        action_id="a1",
        capability_granted=Capability.L2,
        scratch_dir=settings.scratch_dir,
        allowed_roots=[tmp_path, settings.workspace_root, fixtures_dir(settings)],
        deadline=NOW + timedelta(hours=1),
        cancel=CancellationToken(),
        clock=FrozenClock(NOW),
        trash_dir=settings.trash_dir,
        settings=settings,
    )


def _clip(settings: Settings, n: int) -> Path:
    return fixtures_dir(settings) / "clips" / f"clip_{n:02d}.vclip.json"


@pytest.mark.req("FR-607")
async def test_probe_and_transcribe(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    settings = _settings(tmp_path)
    registry = default_registry()
    clip = _clip(settings, 1)
    # Allow reading fixtures by copying path under allowed roots via symlink or re-root.
    # Tests allowlist includes fixtures_dir.
    probe = await registry.get("media.probe").execute(
        registry.get("media.probe").Input(path=str(clip)),
        ctx,
    )
    assert probe.duration_s > 0  # type: ignore[attr-defined]
    transcript = await registry.get("media.transcribe").execute(
        registry.get("media.transcribe").Input(path=str(clip)),
        ctx,
    )
    assert "welcome" in transcript.text.lower()  # type: ignore[attr-defined]
    assert transcript.words  # type: ignore[attr-defined]


@pytest.mark.req("FR-607")
async def test_detect_profanity_and_mute(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    settings = _settings(tmp_path)
    registry = default_registry()
    clip = _clip(settings, 3)
    detected = await registry.get("media.detect_segments").execute(
        registry.get("media.detect_segments").Input(
            path=str(clip), kinds=["profanity", "filler", "silence"]
        ),
        ctx,
    )
    profane = [s for s in detected.segments if s.kind == "profanity"]  # type: ignore[attr-defined]
    assert profane
    dest = settings.scratch_dir / "muted.vclip.json"
    muted = await registry.get("media.mute_segment").execute(
        registry.get("media.mute_segment").Input(
            path=str(clip),
            start=profane[0].start,
            end=profane[0].end,
            dest=str(dest),
        ),
        ctx,
    )
    assert Path(muted.dest).exists()  # type: ignore[attr-defined]
    again = await registry.get("media.transcribe").execute(
        registry.get("media.transcribe").Input(path=str(muted.dest)),  # type: ignore[attr-defined]
        ctx,
    )
    assert "damn" not in again.text.lower()  # type: ignore[attr-defined]


@pytest.mark.req("FR-607")
async def test_cut_concat_caption_export_pipeline(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    settings = _settings(tmp_path)
    registry = default_registry()
    clips = [_clip(settings, i) for i in range(1, 5)]
    concat_dest = settings.scratch_dir / "draft.vclip.json"
    concatenated = await registry.get("media.concat").execute(
        registry.get("media.concat").Input(
            paths=[str(c) for c in clips],
            dest=str(concat_dest),
        ),
        ctx,
    )
    assert concatenated.duration_s > 15  # type: ignore[attr-defined]
    cut_dest = settings.scratch_dir / "sixty.vclip.json"
    cut = await registry.get("media.cut").execute(
        registry.get("media.cut").Input(
            path=str(concatenated.dest),  # type: ignore[attr-defined]
            start=0.0,
            end=min(60.0, float(concatenated.duration_s)),  # type: ignore[attr-defined]
            dest=str(cut_dest),
        ),
        ctx,
    )
    caption_dest = settings.scratch_dir / "captions.srt"
    captioned = await registry.get("media.caption").execute(
        registry.get("media.caption").Input(
            path=str(cut.dest),  # type: ignore[attr-defined]
            dest=str(caption_dest),
            mode="sidecar",
        ),
        ctx,
    )
    assert Path(captioned.srt_path).exists()  # type: ignore[attr-defined]
    assert captioned.cue_count >= 1  # type: ignore[attr-defined]
    export_dest = settings.scratch_dir / "final.mp4"
    exported = await registry.get("media.export").execute(
        registry.get("media.export").Input(
            path=str(cut.dest),  # type: ignore[attr-defined]
            dest=str(export_dest),
        ),
        ctx,
    )
    assert Path(exported.path).exists()  # type: ignore[attr-defined]
    assert exported.sha256  # type: ignore[attr-defined]
    plan = registry.get("media.export").verification_plan(
        registry.get("media.export").Input(path=str(cut.dest), dest=str(export_dest)),  # type: ignore[attr-defined]
        exported,
    )
    assert any(c["type"] == "file_hash" for c in plan)


@pytest.mark.req("FR-607")
async def test_scratch_only_intermediate_writes(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    settings = _settings(tmp_path)
    registry = default_registry()
    clip = _clip(settings, 1)
    outside = tmp_path / "outside.vclip.json"
    with pytest.raises(ToolError) as exc:
        await registry.get("media.cut").execute(
            registry.get("media.cut").Input(path=str(clip), start=0.0, end=1.0, dest=str(outside)),
            ctx,
        )
    assert exc.value.code is ErrorCode.PERMISSION_DENIED


@pytest.mark.req("FR-607")
def test_export_policy_confirms(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    policy = load_policy(
        Path("config/policy.yaml"),
        variables=variables_for(settings.scratch_dir, settings.workspace_root),
    )
    decision = policy.evaluate(
        PolicyRequest(
            tool="media.export",
            level=Capability.L2,
            parameters={"path": "a", "dest": "b"},
        )
    )
    assert decision.decision.value == "CONFIRM"
