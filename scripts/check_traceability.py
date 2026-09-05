"""Every P0 requirement must have at least one linked test (NFR-10).

This is the mechanism that keeps documentation and reality in sync, and it is
the most important process control in the project: without it, the requirements
document quietly becomes fiction.

Requirements are parsed from docs/01-REQUIREMENTS.md. Tests declare coverage
with ``@pytest.mark.req("FR-xxx")``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS_DOC = ROOT / "docs" / "01-REQUIREMENTS.md"
TESTS = ROOT / "tests"

ROW = re.compile(
    r"^\|\s*(?P<id>(?:FR|NFR)-\d+)\s*\|.*?\|\s*(?P<priority>P[012]|pass|bit-identical|[^|]+?)\s*\|"
)
MARKER = re.compile(r"""@pytest\.mark\.req\(\s*["'](?P<id>(?:FR|NFR)-\d+)["']""")


def parse_requirements() -> dict[str, str]:
    requirements: dict[str, str] = {}
    for line in REQUIREMENTS_DOC.read_text(encoding="utf-8").splitlines():
        match = ROW.match(line.strip())
        if match:
            requirements[match.group("id")] = match.group("priority")
    return requirements


def parse_covered() -> set[str]:
    covered: set[str] = set()
    for file in TESTS.rglob("test_*.py"):
        covered.update(m.group("id") for m in MARKER.finditer(file.read_text(encoding="utf-8")))
    return covered


def main() -> int:
    requirements = parse_requirements()
    if not requirements:
        print("No requirements parsed -- check the table format in 01-REQUIREMENTS.md.")
        return 1

    covered = parse_covered()
    p0 = {rid for rid, priority in requirements.items() if priority == "P0"}
    missing = sorted(p0 - covered)
    unknown = sorted(covered - set(requirements))

    print(f"Requirements: {len(requirements)}  (P0: {len(p0)})")
    print(f"Covered:      {len(covered & set(requirements))}")

    if unknown:
        print("\nTests reference unknown requirement IDs:")
        for rid in unknown:
            print(f"  {rid}")

    if missing:
        print(f"\nP0 requirements with no linked test ({len(missing)}):")
        for rid in missing:
            print(f"  {rid}")
        # During early milestones most P0 requirements are not yet implemented.
        # The gate becomes blocking at M5; until then it reports.
        print("\n(reporting only until milestone M5 -- see docs/12-ROADMAP.md)")

    return 1 if unknown else 0


if __name__ == "__main__":
    sys.exit(main())
