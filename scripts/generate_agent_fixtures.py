"""Generate synthetic agent eval tasks."""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "evals" / "fixtures" / "agent" / "tasks.jsonl"


def main() -> None:
    tasks: list[dict[str, str]] = []
    for index in range(50):
        tasks.append(
            {
                "instruction": f"list D:/workspace/project-{index}",
                "expected_tool": "fs.list_dir",
            }
        )
        tasks.append(
            {
                "instruction": f"summarize quarterly report number {index}",
                "expected_tool": "task.report",
            }
        )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        "\n".join(json.dumps(row) for row in tasks[:100]) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {min(100, len(tasks))} tasks to {OUT}")


if __name__ == "__main__":
    main()
