"""M2 demo: an L3 action blocks for approval and proceeds only after a decision.

This is the milestone's exit criterion made visible. Four scenarios, each of
which is a claim the security document makes:

    python demos/m2/run_demo.py                # approve  -> the effect happens once
    python demos/m2/run_demo.py --reject       # reject   -> the action fails, no effect
    python demos/m2/run_demo.py --tamper       # edit after approval -> consent is void
    python demos/m2/run_demo.py --denied-path  # a credential path never asks anyone

Requires Postgres and Redis up (``docker compose -f infra/compose.yaml up -d``)
and the schema migrated (``astra db upgrade``).

The demo registers ``demo.notify``, an L3 non-idempotent tool with a visible
external effect. Nothing in the production catalog is above L1 yet -- M3 brings
the mutating tools -- so demonstrating a gate requires something to gate. It is
declared here rather than by loosening the policy, because a demo that had to
weaken the thing it demonstrates would prove the opposite of the intended point.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any, ClassVar

from pydantic import BaseModel
from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from astra.core.config import Settings, get_settings
from astra.core.logging import configure_logging
from astra.core.types import ActionStatus, Capability, TaskStatus
from astra.orchestrator.approvals import ApprovalWorkflow
from astra.orchestrator.plans import ActionSpec, HandwrittenPlan, PlanError, PlanService, StepSpec
from astra.orchestrator.runtime import (
    get_registry,
    make_queue,
    make_scheduler,
    make_worker,
    reset_registry,
)
from astra.orchestrator.tasks import TaskService
from astra.security.audit import AuditTrail
from astra.store.db import dispose_engine, init_engine, session_scope
from astra.store.models import Action, Approval, AuditLog, Task
from astra.tools.base import Tool, ToolContext
from astra.tools.registry import ToolRegistry, default_registry

console = Console()


class NotifyInput(BaseModel):
    recipient: str
    body: str = ""


class NotifyOutput(BaseModel):
    delivered_to: str


class DemoNotify(Tool):
    """An externally-visible, non-idempotent, irreversible L3 effect."""

    name: ClassVar[str] = "demo.notify"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Pretend to notify someone outside this machine. Demo only."
    Input: ClassVar[type[BaseModel]] = NotifyInput
    Output: ClassVar[type[BaseModel]] = NotifyOutput
    base_capability: ClassVar[Capability] = Capability.L3
    idempotent: ClassVar[bool] = False
    delivered: ClassVar[list[str]] = []

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        assert isinstance(params, NotifyInput)
        return [{"verifier": "value_equals", "field": "delivered_to", "expected": params.recipient}]

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, NotifyInput)
        DemoNotify.delivered.append(params.recipient)
        console.print(f"  [magenta]>> notification actually sent to {params.recipient}[/magenta]")
        return NotifyOutput(delivered_to=params.recipient)


def registry_with_demo_tool() -> ToolRegistry:
    registry = default_registry()
    registry.register(DemoNotify())
    return registry


def notify_plan(recipient: str) -> HandwrittenPlan:
    return HandwrittenPlan(
        steps=[
            StepSpec(
                alias="tell",
                title="Notify the recipient",
                intent="Tell someone outside this machine that grading is done",
                actions=[
                    ActionSpec(
                        alias="n",
                        tool="demo.notify",
                        parameters={"recipient": recipient, "body": "Grades are posted."},
                    )
                ],
            )
        ]
    )


def read_plan(path: str) -> HandwrittenPlan:
    return HandwrittenPlan(
        steps=[
            StepSpec(
                alias="peek",
                title="Read a file",
                intent="Read something the policy protects",
                actions=[
                    ActionSpec(alias="r", tool="fs.read_file", parameters={"path": path})
                ],
            )
        ]
    )


async def sole_action(task_id: str) -> Action:
    async with session_scope() as session:
        return (
            await session.execute(select(Action).where(Action.task_id == task_id))
        ).scalar_one()


async def approvals_for(task_id: str) -> list[Approval]:
    async with session_scope() as session:
        return list(
            (
                await session.execute(
                    select(Approval).where(Approval.task_id == task_id).order_by(Approval.id)
                )
            ).scalars()
        )


async def reload_task(task_id: str) -> Task:
    async with session_scope() as session:
        task = await session.get(Task, task_id)
        assert task is not None
        return task


def show_approval(approval: Approval) -> None:
    table = Table(title=f"approval {approval.id}  [{approval.status}]")
    table.add_column("field")
    table.add_column("value", overflow="fold")
    presented = approval.presented
    table.add_row("summary", approval.summary)
    table.add_row("capability", approval.capability_level.value)
    table.add_row("tool", str(presented.get("tool")))
    table.add_row("parameters", str(presented.get("parameters")))
    table.add_row("intent", str(presented.get("intent")))
    table.add_row("blast radius", str(approval.blast_radius))
    table.add_row("policy", str(presented.get("policy")))
    table.add_row("expires", approval.expires_at.isoformat())
    console.print(table)


async def show_trail(task_id: str) -> None:
    async with session_scope() as session:
        rows = list(
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.task_id == task_id).order_by(AuditLog.id)
                )
            ).scalars()
        )
        report = await AuditTrail().verify(session, start_id=rows[0].id if rows else None)

    table = Table(title="audit trail")
    table.add_column("id", justify="right")
    table.add_column("actor")
    table.add_column("event")
    table.add_column("level")
    for row in rows:
        table.add_row(
            str(row.id),
            row.actor,
            row.event_type,
            row.capability_level.value if row.capability_level else "",
        )
    console.print(table)
    verdict = "[green]chain intact[/green]" if report.ok else f"[red]{report.detail}[/red]"
    console.print(f"{verdict} across {report.rows} records")


async def drive(scheduler: Any, worker: Any, task_id: str, rounds: int = 20) -> Task:
    """Run the loop until the task settles or stops making progress."""
    for _ in range(rounds):
        published = await scheduler.tick()
        worked = await worker.run_once(block_ms=100)
        task = await reload_task(task_id)
        if task.status.is_terminal or task.status is TaskStatus.WAITING_FOR_USER:
            return task
        if published == 0 and not worked:
            await asyncio.sleep(0.1)
    return await reload_task(task_id)


async def run(mode: str) -> int:
    settings: Settings = get_settings()
    configure_logging(settings)
    settings.ensure_directories()
    reset_registry(registry_with_demo_tool())
    DemoNotify.delivered.clear()

    init_engine(settings)
    redis, queue = make_queue(settings)
    scheduler = make_scheduler(settings, queue)
    worker = make_worker(settings, queue, worker_id="demo-m2-worker")

    try:
        await scheduler.recover()

        if mode == "denied-path":
            return await run_denied_path(scheduler, settings)

        async with session_scope() as session:
            task = await TaskService(session, settings).create(
                instruction="Tell the dean that grading is finished.",
                capability_ceiling=Capability.L3,
            )
            task = await PlanService(session, settings, get_registry()).install(
                task, notify_plan("dean@example.edu")
            )
        console.print(f"installed an L3 plan for task [bold]{task.id}[/bold]")

        current = await drive(scheduler, worker, task.id)
        action = await sole_action(task.id)
        console.print(
            f"after one dispatch round: task [bold]{current.status}[/bold], "
            f"action [bold]{action.status}[/bold]"
        )
        if DemoNotify.delivered:
            console.print("[red]FAIL: the effect happened before anyone approved it[/red]")
            return 1
        console.print("[green]nothing was sent[/green] — the gate held")

        approval = (await approvals_for(task.id))[0]
        show_approval(approval)

        if mode == "reject":
            async with session_scope() as session:
                await ApprovalWorkflow(session, settings, get_registry()).reject(
                    approval.id, decided_by="demo:user", reason="wrong recipient"
                )
            console.print("[yellow]rejected[/yellow]")
        else:
            async with session_scope() as session:
                await ApprovalWorkflow(session, settings, get_registry()).approve(
                    approval.id, decided_by="demo:user"
                )
            console.print("[green]approved[/green]")

        if mode == "tamper":
            # The attack the parameter hash exists to stop: consent was granted
            # for one recipient, and the row is edited before dispatch.
            async with session_scope() as session:
                stored = await session.get(Action, action.id)
                assert stored is not None
                stored.parameters = {
                    "recipient": "attacker@example.com",
                    "body": "Grades are posted.",
                }
            console.print("[yellow]tampered[/yellow] with the parameters after approval")

        current = await drive(scheduler, worker, task.id)
        action = await sole_action(task.id)
        console.print(
            f"final: task [bold]{current.status}[/bold], action [bold]{action.status}[/bold], "
            f"delivered={DemoNotify.delivered}"
        )
        await show_trail(task.id)
        return check(mode, current, action, await approvals_for(task.id))
    finally:
        await redis.aclose()
        await dispose_engine()
        reset_registry(None)


async def run_denied_path(scheduler: Any, settings: Settings) -> int:
    """A credential path classifies L4 *and* matches a deny rule. Deny wins, and
    no approval is created: there is nothing a human could usefully consent to."""
    target = str(settings.workspace_root / ".env")
    async with session_scope() as session:
        task = await TaskService(session, settings).create(
            instruction="Read the environment file.", capability_ceiling=Capability.L4
        )
        try:
            task = await PlanService(session, settings, get_registry()).install(
                task, read_plan(target)
            )
        except PlanError as exc:
            console.print(f"[green]refused at plan install[/green]: {exc}")
            return 0

    await scheduler.tick()
    action = await sole_action(task.id)
    approvals = await approvals_for(task.id)
    console.print(
        f"action [bold]{action.status}[/bold] at [bold]{action.capability_level}[/bold], "
        f"approvals requested: {len(approvals)}"
    )
    await show_trail(task.id)
    if action.status is ActionStatus.FAILED and not approvals:
        console.print("[green]denied without asking anyone[/green]")
        return 0
    console.print("[red]FAIL: a denied path should not reach a human[/red]")
    return 1


def check(mode: str, task: Task, action: Action, approvals: list[Approval]) -> int:
    """Each mode has one claim, asserted here so the demo can fail loudly."""
    if mode == "approve":
        ok = action.status is ActionStatus.SUCCEEDED and (
            DemoNotify.delivered == ["dean@example.edu"]
        )
        note = "the approved effect happened exactly once and verification passed"
    elif mode == "reject":
        ok = (
            action.status is ActionStatus.FAILED
            and not DemoNotify.delivered
            and task.status is TaskStatus.FAILED
        )
        note = "a rejection fails the action and the task, with no effect and no retry"
    else:  # tamper
        ok = (
            action.status is ActionStatus.WAITING_FOR_USER
            and not DemoNotify.delivered
            and len(approvals) == 2
            and approvals[0].consumed_at is None
        )
        note = "the edited invocation did not inherit the approval; Astra asked again"

    if ok:
        console.print(f"[green]OK[/green] — {note}")
        return 0
    console.print(f"[red]FAIL[/red] — expected: {note}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--reject", action="store_true", help="Refuse the approval.")
    group.add_argument(
        "--tamper",
        action="store_true",
        help="Approve, then edit the parameters before dispatch.",
    )
    group.add_argument(
        "--denied-path",
        action="store_true",
        help="Target a credential path: denied outright, never presented.",
    )
    args = parser.parse_args()
    mode = "approve"
    if args.reject:
        mode = "reject"
    elif args.tamper:
        mode = "tamper"
    elif args.denied_path:
        mode = "denied-path"
    return asyncio.run(run(mode))


if __name__ == "__main__":
    raise SystemExit(main())
