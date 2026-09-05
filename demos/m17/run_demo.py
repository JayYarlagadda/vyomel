"""M17 demo: gym floor perception → session plan (scenario S8)."""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from vyomel.clients.wearable import task_create_payload
from vyomel.perception.camera import equipment_labels, observe_frame
from vyomel.perception.gym import build_gym_session, default_history

console = Console()


def main() -> None:
    scene = Path("vyomel/perception/fixtures/gym_floor.json")
    frame = observe_frame(scene_path=scene)
    equipment = equipment_labels(frame)
    plan = build_gym_session(frame, preference=default_history())
    payload = task_create_payload(
        "I'm at the gym - look at the equipment and build today's session.",
        context_hints={"equipment": equipment},
    )

    console.print(f"[bold]Detected equipment:[/bold] {', '.join(equipment)}")
    console.print(f"[bold]Focus:[/bold] {plan.focus} · {plan.duration_min} min")
    table = Table("exercise", "equipment", "sets", "reps")
    for block in plan.blocks:
        table.add_row(block.name, block.equipment, str(block.sets), block.reps)
    console.print(table)
    console.print("[dim]Wearable would POST /v1/tasks with:[/dim]")
    console.print_json(json.dumps(payload))


if __name__ == "__main__":
    main()
