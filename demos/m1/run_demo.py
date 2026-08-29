"""M1 demo: a handwritten 5-action DAG executes durably.

Reproduces the milestone's headline claim from a clean checkout, including the
part that is easy to assert and hard to believe: an action whose worker died
mid-flight is recovered and completes exactly once.

    python demos/m1/run_demo.py            # straight run
    python demos/m1/run_demo.py --crash    # abandon a claimed action, recover

Requires Postgres and Redis up (``docker compose -f infra/compose.yaml up -d``)
and the schema migrated (``astra db upgrade``). Everything runs in-process: the
scheduler and worker are the same classes ``astra serve``/``astra worker`` host,
so the demo exercises production wiring rather than a parallel code path.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
from pathlib import Path

from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from astra.core.config import Settings, get_settings
from astra.core.logging import configure_logging
from astra.core.types import ActionStatus, TaskStatus
from astra.orchestrator.plans import ActionSpec, HandwrittenPlan, PlanService, StepSpec
from astra.orchestrator.runtime import get_registry, make_queue, make_scheduler, make_worker
from astra.orchestrator.tasks import TaskService
from astra.runtime.reaper import Reaper
from astra.store.db import dispose_engine, init_engine, session_scope
from astra.store.models import Action, Task

console = Console()

SAMPLE_FILES = {
    "notes.md": "# Notes\n\nThe runtime must not lose work.\n",
    "rubric.txt": "Correctness 40\nDesign 30\nTesting 20\nDocs 10\n",
}


def build_plan(workspace: Path) -> HandwrittenPlan:
    """Five actions over three steps, with a genuine fan-out and a join.

        list ──► read notes ──┐
             └─► read rubric ─┴─► count ──► report

    The two reads are independent, so bounded parallelism has something to do;
    the report joins them, so the DAG is not a disguised linear chain.
    """
    return HandwrittenPlan(
        steps=[
            StepSpec(
                alias="survey",
                title="Survey the workspace",
                intent="See what is available before reading anything",
                actions=[
                    ActionSpec(alias="ls", tool="fs.list_dir", parameters={"path": str(workspace)})
                ],
            ),
            StepSpec(
                alias="gather",
                title="Read both source documents",
                intent="Collect the content the report will summarize",
                depends_on=["survey"],
                actions=[
                    ActionSpec(
                        alias="read_notes",
                        tool="fs.read_file",
                        parameters={"path": str(workspace / "notes.md")},
                    ),
                    ActionSpec(
                        alias="read_rubric",
                        tool="fs.read_file",
                        parameters={"path": str(workspace / "rubric.txt")},
                    ),
                ],
            ),
            StepSpec(
                alias="summarize",
                title="Report",
                intent="Record what was found",
                depends_on=["gather"],
                actions=[
                    ActionSpec(
                        alias="recheck",
                        tool="fs.list_dir",
                        parameters={"path": str(workspace)},
                    ),
                    ActionSpec(
                        alias="report",
                        tool="task.report",
                        parameters={
                            "summary": "Read the workspace notes and rubric.",
                            "findings": ["notes.md read", "rubric.txt read"],
                        },
                        depends_on=["read_notes", "read_rubric", "recheck"],
                    ),
                ],
            ),
        ]
    )


def prepare_workspace(settings: Settings) -> Path:
    workspace = settings.workspace_root / "demo-m1"
    workspace.mkdir(parents=True, exist_ok=True)
    for name, content in SAMPLE_FILES.items():
        (workspace / name).write_text(content, encoding="utf-8")
    return workspace


async def abandon_one_running_action(task_id: str) -> str | None:
    """Simulate ``kill -9`` between claim and result: RUNNING with a dead lease.

    A real SIGKILL of a separate worker process is the M6 chaos harness. The
    state it leaves behind is exactly this row, and this row is what recovery
    has to handle.
    """
    async with session_scope() as session:
        action = (
            await session.execute(
                select(Action)
                .where(Action.task_id == task_id, Action.status == ActionStatus.DISPATCHED)
                .limit(1)
            )
        ).scalar_one_or_none()
        if action is None:
            return None
        action.status = ActionStatus.RUNNING
        action.lease_owner = "worker-that-died"
        action.lease_until = action.created_at - timedelta(seconds=1)
        action.attempt_count = 1
        return action.id


async def load(task_id: str) -> tuple[Task, list[Action]]:
    async with session_scope() as session:
        task = await session.get(Task, task_id)
        assert task is not None
        actions = list(
            (await session.execute(select(Action).where(Action.task_id == task_id))).scalars()
        )
        return task, actions


def render(task: Task, actions: list[Action]) -> Table:
    table = Table(title=f"task {task.id}  [{task.status}]")
    table.add_column("tool")
    table.add_column("status")
    table.add_column("attempts", justify="right")
    for action in sorted(actions, key=lambda a: a.id):
        color = {
            ActionStatus.SUCCEEDED: "green",
            ActionStatus.FAILED: "red",
            ActionStatus.CANCELLED: "red",
        }.get(action.status, "yellow")
        table.add_row(action.tool, f"[{color}]{action.status}[/{color}]", str(action.attempt_count))
    return table


async def run(*, crash: bool) -> int:
    settings = get_settings()
    configure_logging(settings)
    settings.ensure_directories()
    workspace = prepare_workspace(settings)
    if not any(workspace.is_relative_to(root) for root in settings.allowed_roots):
        console.print(
            f"[red]{workspace} is outside ASTRA_ALLOWED_ROOTS[/red] — "
            "the sandbox will reject every read. Add the workspace root to .env."
        )
        return 1

    init_engine(settings)
    redis, queue = make_queue(settings)
    scheduler = make_scheduler(settings, queue)
    worker = make_worker(settings, queue, worker_id="demo-worker")
    crashed_at: str | None = None

    try:
        async with session_scope() as session:
            task = await TaskService(session, settings).create(
                instruction="Summarize the demo workspace."
            )
            task = await PlanService(session, settings, get_registry()).install(
                task, build_plan(workspace)
            )
        console.print(f"installed a 5-action plan for task [bold]{task.id}[/bold]")

        # Same entry point ``astra serve`` uses: creates the consumer group and
        # re-drives anything a previous run committed but never published.
        await scheduler.recover()

        current, actions = await load(task.id)
        for _ in range(60):
            published = await scheduler.tick()
            if crash and crashed_at is None and published:
                crashed_at = await abandon_one_running_action(task.id)
                if crashed_at is not None:
                    console.print(
                        f"[yellow]simulated worker death[/yellow] holding action {crashed_at}"
                    )
                    await Reaper().reap()
                    continue
            worked = await worker.run_once(block_ms=100)
            current, actions = await load(task.id)
            if current.status.is_terminal:
                break
            if published == 0 and not worked:
                # Nothing to do this round: everything left is waiting out a backoff.
                await asyncio.sleep(0.2)

        console.print(render(current, actions))
        if crashed_at is not None:
            recovered = next(a for a in actions if a.id == crashed_at)
            console.print(
                f"recovered action {crashed_at}: [bold]{recovered.status}[/bold] "
                f"after {recovered.attempt_count} attempts"
            )
        if current.status is TaskStatus.SUCCEEDED:
            console.print(f"[green]task succeeded[/green] result={current.result}")
            return 0
        console.print(f"[red]task ended {current.status}[/red] error={current.error}")
        return 1
    finally:
        await redis.aclose()
        await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--crash",
        action="store_true",
        help="Abandon one claimed action mid-DAG and let recovery finish the task.",
    )
    args = parser.parse_args()
    return asyncio.run(run(crash=args.crash))


if __name__ == "__main__":
    raise SystemExit(main())
