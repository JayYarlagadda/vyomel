"""Workflow learning eval: mine recurring pipelines, propose, accept, invoke."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vyomel.core.types import Capability
from vyomel.learning.service import mine_and_propose
from vyomel.learning.signatures import ObservedAction
from vyomel.learning.store import (
    accept_workflow,
    expand_workflow,
    get_workflow_store,
    reject_workflow,
    reset_workflow_store,
)


def _actions(n: int = 5) -> list[ObservedAction]:
    out: list[ObservedAction] = []
    for i in range(n):
        root = f"/workspace/p{i}"
        tid = f"task{i}"
        out.extend(
            [
                ObservedAction(tool="fs.list_dir", parameters={"path": root}, task_id=tid),
                ObservedAction(
                    tool="fs.read_file",
                    parameters={"path": f"{root}/in.md"},
                    task_id=tid,
                ),
                ObservedAction(
                    tool="fs.write_file",
                    parameters={"path": f"{root}/out.md", "content": f"body-{i}"},
                    task_id=tid,
                ),
                ObservedAction(
                    tool="task.report",
                    parameters={"summary": f"done-{i}"},
                    task_id=tid,
                ),
            ]
        )
    return out


def run() -> dict[str, object]:
    reset_workflow_store()
    store = get_workflow_store()
    proposals = mine_and_propose(
        _actions(5),
        min_support=3,
        tool_capabilities={
            "fs.list_dir": Capability.L0,
            "fs.read_file": Capability.L0,
            "fs.write_file": Capability.L1,
            "task.report": Capability.L0,
        },
        store=store,
    )
    if not proposals:
        raise RuntimeError("expected at least one proposal")
    proposal = proposals[0]
    if proposal.status != "proposed":
        raise RuntimeError("new proposals must start unaccepted")

    # Reject path suppresses; re-mine should not recreate the same pattern.
    rejected = reject_workflow(store, proposal.id)
    if rejected.status != "rejected":
        raise RuntimeError("reject failed")
    after_reject = mine_and_propose(_actions(5), store=store)
    if any(p.pattern_key == proposal.pattern_key for p in after_reject):
        raise RuntimeError("suppressed pattern was re-proposed")

    reset_workflow_store()
    store = get_workflow_store()
    proposals = mine_and_propose(_actions(5), store=store)
    proposal = proposals[0]
    accepted = accept_workflow(store, proposal.id)
    values = {p.name: f"bound-{p.name}" for p in accepted.parameters}
    steps = expand_workflow(store, accepted.id, values)
    if any("$param" in str(step) for step in steps):
        raise RuntimeError("unbound parameters remain after expand")

    return {
        "success": True,
        "proposals": len(proposals),
        "occurrence_count": accepted.occurrence_count,
        "steps": len(steps),
        "parameters": len(accepted.parameters),
        "trust_level": accepted.trust_level.value,
        "accepted": True,
        "suppression_ok": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("evals/results/2026-09-04-m15"))
    args = parser.parse_args()
    result = run()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    report = f"""# M15 workflow learning — 2026-09-04

Mine recurring action pipelines (support >= 3), propose parameterized workflows,
require explicit acceptance before invoke, suppress rejected patterns.

## Results

| metric | value |
|---|---:|
| success | {result["success"]} |
| proposals | {result["proposals"]} |
| occurrence_count | {result["occurrence_count"]} |
| parameters | {result["parameters"]} |
| expanded steps | {result["steps"]} |
| trust_level | {result["trust_level"]} |
| suppression_ok | {result["suppression_ok"]} |

## Reproduce

```powershell
python evals/suites/learning/run.py
pytest tests/learning tests/security/test_trusted_workflows.py
```
"""
    (args.out / "report.md").write_text(report, encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
