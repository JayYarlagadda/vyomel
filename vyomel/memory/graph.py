"""Personal context graph: entities, relations, and hard delete (FR-502, FR-509)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from vyomel.core.errors import NotFoundError
from vyomel.core.ids import new_id
from vyomel.core.types import EntityType
from vyomel.memory.episodes import delete_episodes_for_entity
from vyomel.store.models import Document, Entity, EntityRelation


@dataclass(frozen=True, slots=True)
class ForgetReport:
    entity_id: str
    documents_deleted: int
    chunks_deleted: int
    relations_deleted: int
    episodes_deleted: int = 0


async def upsert_document_entity(
    session: AsyncSession,
    *,
    document: Document,
    path: Path,
) -> Entity:
    """Ensure every ingested document has a graph node (structural source, confidence 1.0)."""
    now = datetime.now(UTC)
    stored = path.as_posix()
    name = path.stem
    aliases = [path.name]

    if document.entity_id:
        existing = await session.get(Entity, document.entity_id)
        if existing is not None:
            existing.last_seen_at = now
            existing.attributes = {**existing.attributes, "path": stored}
            if path.name not in existing.aliases:
                existing.aliases = [*existing.aliases, path.name]
            return existing

    entity = Entity(
        id=new_id(),
        type=EntityType.DOCUMENT,
        name=name,
        aliases=aliases,
        attributes={"path": stored},
        salience=1.0,
        source="structural",
        first_seen_at=now,
        last_seen_at=now,
    )
    session.add(entity)
    document.entity_id = entity.id
    return entity


async def get_entity(session: AsyncSession, entity_id: str) -> Entity:
    entity = await session.scalar(
        select(Entity)
        .options(
            selectinload(Entity.outgoing_relations).selectinload(EntityRelation.to_entity),
            selectinload(Entity.incoming_relations).selectinload(EntityRelation.from_entity),
            selectinload(Entity.documents).selectinload(Document.chunks),
        )
        .where(Entity.id == entity_id)
    )
    if entity is None:
        raise NotFoundError(f"entity {entity_id} not found", detail={"entity_id": entity_id})
    return entity


async def forget_entity(session: AsyncSession, entity_id: str) -> ForgetReport:
    """Hard-delete an entity, its documents, chunks, and relations (FR-509)."""
    entity = await session.scalar(
        select(Entity)
        .options(
            selectinload(Entity.documents).selectinload(Document.chunks),
            selectinload(Entity.outgoing_relations),
            selectinload(Entity.incoming_relations),
        )
        .where(Entity.id == entity_id)
    )
    if entity is None:
        raise NotFoundError(f"entity {entity_id} not found", detail={"entity_id": entity_id})

    documents_deleted = len(entity.documents)
    chunks_deleted = sum(len(document.chunks) for document in entity.documents)
    relations_deleted = len(entity.outgoing_relations) + len(entity.incoming_relations)
    episodes_deleted = await delete_episodes_for_entity(session, entity_id)

    await session.execute(
        delete(EntityRelation).where(
            or_(EntityRelation.from_id == entity_id, EntityRelation.to_id == entity_id)
        )
    )
    for document in list(entity.documents):
        await session.delete(document)
    await session.delete(entity)

    return ForgetReport(
        entity_id=entity_id,
        documents_deleted=documents_deleted,
        chunks_deleted=chunks_deleted,
        relations_deleted=relations_deleted,
        episodes_deleted=episodes_deleted,
    )


async def remember_entity(
    session: AsyncSession,
    *,
    entity_type: EntityType,
    name: str,
    aliases: list[str] | None = None,
    attributes: dict[str, Any] | None = None,
) -> Entity:
    """Persist an explicit user fact at confidence 1.0 (docs/08 §2)."""
    now = datetime.now(UTC)
    normalized_aliases = list(dict.fromkeys([name, *(aliases or [])]))
    existing = await session.scalar(
        select(Entity).where(Entity.type == entity_type, Entity.name == name)
    )
    if existing is not None:
        existing.last_seen_at = now
        existing.attributes = {**existing.attributes, **(attributes or {})}
        merged = list(dict.fromkeys([*existing.aliases, *normalized_aliases]))
        existing.aliases = merged
        return existing

    entity = Entity(
        id=new_id(),
        type=entity_type,
        name=name,
        aliases=normalized_aliases,
        attributes=attributes or {},
        salience=1.0,
        source="explicit",
        first_seen_at=now,
        last_seen_at=now,
    )
    session.add(entity)
    return entity


async def list_entities(
    session: AsyncSession,
    *,
    entity_type: EntityType | None = None,
    query: str | None = None,
    limit: int = 50,
) -> list[Entity]:
    stmt = select(Entity).order_by(Entity.salience.desc(), Entity.last_seen_at.desc()).limit(limit)
    if entity_type is not None:
        stmt = stmt.where(Entity.type == entity_type)
    if query:
        pattern = f"%{query.casefold()}%"
        stmt = stmt.where(Entity.name.ilike(pattern))
    return list((await session.scalars(stmt)).all())
