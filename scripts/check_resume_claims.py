#!/usr/bin/env python3
"""Verify resume-claim evidence artifacts exist (docs/14-RESUME-MAPPING.md).

Does not re-run the full suite — CI already does. This is a cheap drift check
that linked paths and eval reports are present for closed claims.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Paths that must exist for ☑ claims flipped in the resume-truth pass.
REQUIRED = [
    ROOT / "tests" / "planner" / "test_decompose.py",
    ROOT / "tests" / "planner" / "test_replan_bounds.py",
    ROOT / "tests" / "runtime" / "test_longrun.py",
    ROOT / "evals" / "suites" / "agent" / "run.py",
    ROOT / "evals" / "results" / "2026-09-02-m5" / "report.md",
    ROOT / "evals" / "results" / "2026-09-02-m6" / "report.md",
    ROOT / "vyomel" / "learning" / "pg_store.py",
    ROOT / "vyomel" / "store" / "migrations" / "versions" / "0009_workflows.py",
]


def main() -> int:
    missing = [str(p.relative_to(ROOT)) for p in REQUIRED if not p.exists()]
    if missing:
        print("missing resume-claim artifacts:", file=sys.stderr)
        for path in missing:
            print(f"  - {path}", file=sys.stderr)
        return 1
    report = (ROOT / "evals" / "results" / "2026-09-02-m5" / "report.md").read_text(
        encoding="utf-8"
    )
    for needle in ("schema_validity_rate", "multi_step_accuracy"):
        if needle not in report:
            print(f"agent eval report missing {needle}", file=sys.stderr)
            return 1
    print(f"ok: {len(REQUIRED)} resume-claim artifacts present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
