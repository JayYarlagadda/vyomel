"""Scenario S7 eval (docs/00 §S7, M14): 12 clips → ~60s draft, mute profanity, caption, export."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vyomel.core.cancel import CancellationToken
from vyomel.core.clock import FrozenClock
from vyomel.core.config import Settings
from vyomel.core.types import Capability, Decision
from vyomel.security.capability import Invocation, classify
from vyomel.security.policy import PolicyRequest, load_policy, variables_for
from vyomel.tools.base import Tool, ToolContext
from vyomel.tools.media.session import fixtures_dir
from vyomel.tools.registry import default_registry

NOW = datetime(2026, 9, 4, 16, 0, tzinfo=UTC)


def _settings(work: Path) -> Settings:
    settings = Settings(
        env="test",
        workspace_root=work / ".vyomel",
        allowed_roots=[work, work / ".vyomel"],
        media_backend="fixture",
        media_fixtures_dir=Path("vyomel/tools/media/fixtures"),
    )
    settings.ensure_directories()
    return settings


def _ctx(settings: Settings, work: Path) -> ToolContext:
    return ToolContext(
        task_id="s7-eval",
        action_id="a1",
        capability_granted=Capability.L2,
        scratch_dir=settings.scratch_dir,
        allowed_roots=[work, settings.workspace_root],
        deadline=NOW + timedelta(hours=2),
        cancel=CancellationToken(),
        clock=FrozenClock(NOW),
        trash_dir=settings.trash_dir,
        settings=settings,
    )


def _gate(tool: Tool, params, policy) -> dict[str, str]:
    parsed = params.model_dump(mode="json")
    classification = classify(
        Invocation(
            tool=tool.name,
            parameters=parsed,
            base=tool.classify(params),
            actuation_tier=tool.actuation_tier,
        ),
        policy.escalation,
    )
    decision = policy.evaluate(
        PolicyRequest(tool=tool.name, level=classification.level, parameters=parsed)
    )
    return {
        "tool": tool.name,
        "level": classification.level.value,
        "decision": decision.decision.value,
        "rule_id": decision.rule_id or "",
    }


async def run_s7(work: Path) -> dict[str, object]:
    settings = _settings(work)
    clips_src = fixtures_dir(settings) / "clips"
    clips_dst = work / "clips"
    clips_dst.mkdir(parents=True, exist_ok=True)
    for src in sorted(clips_src.glob("clip_*.vclip.json")):
        shutil.copy2(src, clips_dst / src.name)

    registry = default_registry()
    ctx = _ctx(settings, work)
    policy = load_policy(
        Path("config/policy.yaml"),
        variables=variables_for(settings.scratch_dir, settings.workspace_root),
    )

    gates: list[dict[str, str]] = []
    steps = 0

    async def step(name: str, payload: dict) -> object:
        nonlocal steps
        steps += 1
        tool = registry.get(name)
        params = tool.Input.model_validate(payload)
        gate = _gate(tool, params, policy)
        gates.append(gate)
        if gate["decision"] == Decision.DENY.value:
            raise RuntimeError(f"{name} denied by policy: {gate}")
        # Eval continues after CONFIRM as if the operator approved.
        return await tool.execute(params, ctx)

    clip_paths = [str(clips_dst / f"clip_{i:02d}.vclip.json") for i in range(1, 13)]
    durations: list[float] = []
    for path in clip_paths:
        probed = await step("media.probe", {"path": path})
        durations.append(float(probed.duration_s))  # type: ignore[attr-defined]

    concat_dest = str(settings.scratch_dir / "all.vclip.json")
    concatenated = await step("media.concat", {"paths": clip_paths, "dest": concat_dest})
    total = float(concatenated.duration_s)  # type: ignore[attr-defined]
    if total < 60:
        raise RuntimeError(f"expected >= 60s of source material, got {total}")

    draft_dest = str(settings.scratch_dir / "draft60.vclip.json")
    draft = await step(
        "media.cut",
        {"path": concat_dest, "start": 0.0, "end": 60.0, "dest": draft_dest},
    )
    draft_dur = float(draft.duration_s)  # type: ignore[attr-defined]
    if abs(draft_dur - 60.0) > 0.05:
        raise RuntimeError(f"draft duration {draft_dur} != 60")

    transcript = await step("media.transcribe", {"path": draft_dest})
    detected = await step(
        "media.detect_segments",
        {
            "path": draft_dest,
            "kinds": ["profanity", "filler", "silence", "highlight"],
            "words": [w.model_dump() for w in transcript.words],  # type: ignore[attr-defined]
        },
    )
    profane = [s for s in detected.segments if s.kind == "profanity"]  # type: ignore[attr-defined]
    if not profane:
        raise RuntimeError("expected at least one profanity segment in the 12-clip corpus")

    current = draft_dest
    muted_count = 0
    for idx, seg in enumerate(profane):
        dest = str(settings.scratch_dir / f"muted_{idx}.vclip.json")
        muted = await step(
            "media.mute_segment",
            {"path": current, "start": seg.start, "end": seg.end, "dest": dest},
        )
        current = str(muted.dest)  # type: ignore[attr-defined]
        muted_count += 1

    cleaned = await step("media.transcribe", {"path": current})
    if any(tok in cleaned.text.lower() for tok in ("damn", "hell")):  # type: ignore[attr-defined]
        raise RuntimeError("profanity still present after mute")

    captioned = await step(
        "media.caption",
        {
            "path": current,
            "dest": str(settings.scratch_dir / "draft.srt"),
            "mode": "sidecar",
            "words": [w.model_dump() for w in cleaned.words],  # type: ignore[attr-defined]
        },
    )
    if int(captioned.cue_count) < 1:  # type: ignore[attr-defined]
        raise RuntimeError("expected caption cues")

    exported = await step(
        "media.export",
        {"path": current, "dest": str(settings.scratch_dir / "s7_final.mp4")},
    )
    export_gate = gates[-1]
    if export_gate["decision"] != Decision.CONFIRM.value:
        raise RuntimeError(f"media.export must CONFIRM, got {export_gate}")

    return {
        "success": True,
        "clip_count": len(clip_paths),
        "source_duration_s": round(total, 3),
        "draft_duration_s": round(draft_dur, 3),
        "profanity_segments": len(profane),
        "muted_segments": muted_count,
        "caption_cues": int(captioned.cue_count),  # type: ignore[attr-defined]
        "export_bytes": int(exported.bytes),  # type: ignore[attr-defined]
        "export_sha256": exported.sha256,  # type: ignore[attr-defined]
        "steps": steps,
        "gates": gates,
        "backend": "fixture",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("evals/results/2026-09-04-m14"),
    )
    args = parser.parse_args()
    import tempfile

    with tempfile.TemporaryDirectory(prefix="vyomel-s7-") as tmp:
        result = asyncio.run(run_s7(Path(tmp)))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    report = f"""# M14 media scenario S7 — 2026-09-04

Fixture clips (12). Concatenate, cut a 60s draft, mute profanity,
add sidecar captions, export.

## Results

| metric | value |
|---|---:|
| success | {result["success"]} |
| clips | {result["clip_count"]} |
| source duration (s) | {result["source_duration_s"]} |
| draft duration (s) | {result["draft_duration_s"]} |
| profanity segments muted | {result["muted_segments"]} |
| caption cues | {result["caption_cues"]} |
| steps | {result["steps"]} |
| export CONFIRM | true |

Exit criterion (S7 end-to-end on the shared runtime) met on the fixture backend.

## Reproduce

```powershell
python evals/suites/media/run.py
pytest tests/tools/test_media.py
```
"""
    (args.out / "report.md").write_text(report, encoding="utf-8")
    print(json.dumps({k: result[k] for k in result if k != "gates"}, indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
