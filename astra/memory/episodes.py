"""Episodic memory: what the agent did, when, and with what outcome (FR-507)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from astra.core.config import Settings
from astra.core.ids import new_id
from astra.core.types import ActionStatus, TaskStatus
from astra.models.embeddings import Embedder, get_embedder
from astra.store.blobs import resolve_result
from astra.store.models import Action, Episode, Task


@dataclass(frozen=True, slots=True)
class EpisodeRecord:
    id: str
    task_id: str
    summary: str
    outcome: str
    entity_ids: tuple[str, ...]
    tools_used: tuple[str, ...]
    started_at: datetime
    finished_at: datetime


async def record_episode(
    session: AsyncSession,
    *,
    task: Task,
    actions: list[Action],
    settings: Settings,
    embedder: Embedder | None = None,
) -> EpisodeRecord | None:
    if task.status is not TaskStatus.SUCCEEDED:
        return None

    encoder = embedder or get_embedder(settings)
    reports = [
        resolve_result(action.result, blob_dir=settings.blob_dir)
        for action in actions
        if action.tool == "task.report"
        and action.status is ActionStatus.SUCCEEDED
        and action.result
    ]
    summary = ""
    if reports:
        summary = str(reports[-1].get("summary", "")).strip()
    if not summary:
        tools = sorted(
            {action.tool for action in actions if action.status is ActionStatus.SUCCEEDED}
        )
        summary = f"Task {task.id} finished with {len(tools)} tools: {', '.join(tools)}"

    tools_used = sorted(
        {action.tool for action in actions if action.status is ActionStatus.SUCCEEDED}
    )
    finished_at = task.finished_at or task.created_at
    started_at = task.started_at or task.created_at
    vector = encoder.embed([summary])[0] if summary else [0.0] * encoder.dimensions

    episode = Episode(
        id=new_id(),
        task_id=task.id,
        entity_ids=[],
        summary=summary,
        outcome=task.status.value,
        tools_used=tools_used,
        started_at=started_at,
        finished_at=finished_at,
        embedding=vector,
        embedding_model=encoder.name,
    )
    session.add(episode)
    return EpisodeRecord(
        id=episode.id,
        task_id=episode.task_id,
        summary=episode.summary,
        outcome=episode.outcome,
        entity_ids=tuple(episode.entity_ids),
        tools_used=tuple(episode.tools_used),
        started_at=episode.started_at,
        finished_at=episode.finished_at,
    )


async def list_episodes(
    session: AsyncSession,
    *,
    entity_id: str | None = None,
    since: datetime | None = None,
    limit: int = 50,
) -> list[Episode]:
    stmt = select(Episode).order_by(Episode.finished_at.desc()).limit(limit)
    if entity_id is not None:
        stmt = stmt.where(Episode.entity_ids.contains([entity_id]))
    if since is not None:
        stmt = stmt.where(Episode.finished_at >= since)
    return list((await session.scalars(stmt)).all())


async def delete_episodes_for_entity(session: AsyncSession, entity_id: str) -> int:
    result = await session.execute(delete(Episode).where(Episode.entity_ids.contains([entity_id])))
    return int(getattr(result, "rowcount", 0) or 0)
