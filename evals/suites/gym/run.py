"""Scenario S8: gym floor perception → today's session (M17)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vyomel.clients.wearable import task_create_payload
from vyomel.perception.camera import equipment_labels, observe_frame
from vyomel.perception.gym import build_gym_session, default_history


def run() -> dict[str, object]:
    frame = observe_frame(scene_path=Path("vyomel/perception/fixtures/gym_floor.json"))
    equipment = equipment_labels(frame)
    plan = build_gym_session(frame, preference=default_history())
    payload = task_create_payload(
        "I'm at the gym - look at the equipment and build today's session.",
        context_hints={"equipment": equipment, "focus": plan.focus},
    )
    if not plan.blocks:
        raise RuntimeError("empty session plan")
    if "barbell" not in equipment:
        raise RuntimeError("expected barbell in gym fixture")
    return {
        "success": True,
        "equipment": equipment,
        "focus": plan.focus,
        "blocks": len(plan.blocks),
        "duration_min": plan.duration_min,
        "wearable_origin": payload["origin"],
        "wearable_client": payload["context_hints"]["client"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("evals/results/2026-09-04-m17"))
    args = parser.parse_args()
    result = run()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    report = f"""# M17 multimodal S8 — 2026-09-04

Camera fixture detects gym equipment; personal history shapes today's session;
wearable client posts through the same HTTP task contract.

## Results

| metric | value |
|---|---:|
| success | {result["success"]} |
| equipment count | {len(result["equipment"])} |
| focus | {result["focus"]} |
| blocks | {result["blocks"]} |
| wearable origin | {result["wearable_origin"]} |

## Reproduce

```powershell
python evals/suites/gym/run.py
pytest tests/perception tests/clients/test_wearable.py
python demos/m17/run_demo.py
```
"""
    (args.out / "report.md").write_text(report, encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
