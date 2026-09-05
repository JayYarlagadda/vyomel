"""Mine -> propose -> store orchestration for workflow learning (FR-901-903)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vyomel.core.config import Settings
from vyomel.core.types import ActionStatus, Capability, TaskStatus
from vyomel.learning.mining import mine_frequent_sequences, sequences_from_actions
from vyomel.learning.pg_store import PostgresWorkflowStore
from vyomel.learning.proposal import WorkflowProposal, propose_workflows
from vyomel.learning.signatures import ObservedAction
from vyomel.learning.store import WorkflowStore, get_workflow_store
from vyomel.store.models import Action, Task


def mine_and_propose(
    actions: list[ObservedAction],
    *,
    min_support: int = 3,
    min_length: int = 3,
    tool_capabilities: dict[str, Capability] | None = None,
    store: WorkflowStore | None = None,
) -> list[WorkflowProposal]:
    """Run the full learning pipeline and persist new/updated proposals (memory)."""
    target = store or get_workflow_store()
    proposals = _propose(
        actions,
        min_support=min_support,
        min_length=min_length,
        tool_capabilities=tool_capabilities,
    )
    saved: list[WorkflowProposal] = []
    for proposal in proposals:
        if target.is_suppressed(proposal.pattern_key):
            continue
        saved.append(target.put(proposal))
    return saved


async def mine_and_propose_pg(
    session: AsyncSession,
    actions: list[ObservedAction],
    *,
    min_support: int = 3,
    min_length: int = 3,
    tool_capabilities: dict[str, Capability] | None = None,
) -> list[WorkflowProposal]:
    """Mine and persist proposals into Postgres (durable path)."""
    store = PostgresWorkflowStore(session)
    proposals = _propose(
        actions,
        min_support=min_support,
        min_length=min_length,
        tool_capabilities=tool_capabilities,
    )
    saved: list[WorkflowProposal] = []
    for proposal in proposals:
        if await store.is_suppressed(proposal.pattern_key):
            continue
        saved.append(await store.put(proposal))
    return saved


def _propose(
    actions: list[ObservedAction],
    *,
    min_support: int,
    min_length: int,
    tool_capabilities: dict[str, Capability] | None,
) -> list[WorkflowProposal]:
    grouped = sequences_from_actions(actions)
    by_task: dict[str, list[ObservedAction]] = {}
    order: list[str] = []
    for action in actions:
        tid = action.task_id or "_anon"
        if tid not in by_task:
            order.append(tid)
            by_task[tid] = []
        by_task[tid].append(action)
    corpus = [(tid, by_task[tid]) for tid in order]
    patterns = mine_frequent_sequences(grouped, min_support=min_support, min_length=min_length)
    return propose_workflows(
        patterns,
        corpus,
        min_support=min_support,
        tool_capabilities=tool_capabilities,
    )


def actions_from_records(records: list[dict[str, Any]]) -> list[ObservedAction]:
    """Coerce audit/action dicts into ObservedAction rows."""
    out: list[ObservedAction] = []
    for row in records:
        tool = row.get("tool")
        if not isinstance(tool, str) or not tool:
            continue
        params = row.get("parameters") or row.get("args") or {}
        if not isinstance(params, dict):
            params = {}
        out.append(
            ObservedAction(
                tool=tool,
                parameters=params,
                task_id=row.get("task_id"),
            )
        )
    return out


async def load_succeeded_actions(
    session: AsyncSession, *, max_tasks: int = 100
) -> list[ObservedAction]:
    """Load recent succeeded-task actions for post-completion mining."""
    task_ids = (
        (
            await session.execute(
                select(Task.id)
                .where(Task.status == TaskStatus.SUCCEEDED)
                .order_by(Task.finished_at.desc().nulls_last(), Task.created_at.desc())
                .limit(max_tasks)
            )
        )
        .scalars()
        .all()
    )
    if not task_ids:
        return []
    rows = (
        (
            await session.execute(
                select(Action)
                .where(
                    Action.task_id.in_(list(task_ids)),
                    Action.status == ActionStatus.SUCCEEDED,
                )
                .order_by(Action.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        ObservedAction(
            tool=row.tool,
            parameters=dict(row.parameters or {}),
            task_id=row.task_id,
        )
        for row in rows
    ]


async def auto_mine_after_task(
    session: AsyncSession,
    *,
    settings: Settings,
    tool_capabilities: dict[str, Capability] | None = None,
) -> list[WorkflowProposal]:
    """Hook after task success: re-mine corpus and persist durable proposals."""
    if not settings.workflow_auto_mine:
        return []
    actions = await load_succeeded_actions(session, max_tasks=settings.workflow_mine_max_tasks)
    if len({a.task_id for a in actions if a.task_id}) < settings.workflow_mine_min_support:
        return []
    if settings.workflow_store_backend == "postgres":
        return await mine_and_propose_pg(
            session,
            actions,
            min_support=settings.workflow_mine_min_support,
            min_length=settings.workflow_mine_min_length,
            tool_capabilities=tool_capabilities,
        )
    return mine_and_propose(
        actions,
        min_support=settings.workflow_mine_min_support,
        min_length=settings.workflow_mine_min_length,
        tool_capabilities=tool_capabilities,
    )
