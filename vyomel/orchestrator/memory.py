"""Memory use cases: ingest and hybrid query.

The API and CLI never import ``vyomel.memory``. This module is the seam that
keeps ingest/retrieve behind the orchestrator layering line.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from vyomel.core.config import Settings
from vyomel.core.errors import ErrorCode, VyomelError
from vyomel.core.types import EntityType
from vyomel.memory.episodes import list_episodes
from vyomel.memory.graph import (
    ForgetReport,
    forget_entity,
    get_entity,
    remember_entity,
)
from vyomel.memory.graph import (
    list_entities as query_entities,
)
from vyomel.memory.ingest import IngestReport, ingest_paths
from vyomel.memory.retrieve import Retrieval, retrieve
from vyomel.models.embeddings import Embedder, get_embedder
from vyomel.store.models import Entity, Episode

Strategy = Literal["hybrid", "vector", "lexical"]


class MemoryService:
    def __init__(self, settings: Settings, embedder: Embedder | None = None) -> None:
        self._settings = settings
        self._embedder = embedder or get_embedder(settings)

    async def ingest(
        self,
        session: AsyncSession,
        paths: Sequence[str],
        *,
        recursive: bool = False,
        watch: bool = False,
    ) -> IngestReport:
        if watch:
            raise VyomelError(
                "filesystem watch is not implemented in this slice",
                code=ErrorCode.UNSUPPORTED,
            )
        if not paths:
            raise VyomelError("at least one path is required", code=ErrorCode.INVALID_PARAMETERS)
        return await ingest_paths(
            session,
            paths,
            self._settings.allowed_roots,
            recursive=recursive,
            embedder=self._embedder,
        )

    async def query(
        self,
        session: AsyncSession,
        query: str,
        *,
        k: int = 10,
        strategy: Strategy = "hybrid",
    ) -> Retrieval:
        stripped = query.strip()
        if not stripped:
            raise VyomelError("query must not be empty", code=ErrorCode.INVALID_PARAMETERS)
        return await retrieve(
            session,
            stripped,
            k=k,
            strategy=strategy,
            embedder=self._embedder,
        )

    async def fetch_entity(self, session: AsyncSession, entity_id: str) -> Entity:
        return await get_entity(session, entity_id)

    async def list_entities(
        self,
        session: AsyncSession,
        *,
        entity_type: EntityType | None = None,
        query: str | None = None,
        limit: int = 50,
    ) -> list[Entity]:
        return await query_entities(session, entity_type=entity_type, query=query, limit=limit)

    async def remember(
        self,
        session: AsyncSession,
        *,
        entity_type: EntityType,
        name: str,
        aliases: list[str] | None = None,
        attributes: dict[str, object] | None = None,
    ) -> Entity:
        return await remember_entity(
            session,
            entity_type=entity_type,
            name=name,
            aliases=aliases,
            attributes=attributes,
        )

    async def forget(self, session: AsyncSession, entity_id: str) -> ForgetReport:
        return await forget_entity(session, entity_id)

    async def fetch_episodes(
        self,
        session: AsyncSession,
        *,
        entity_id: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[Episode]:
        return await list_episodes(session, entity_id=entity_id, since=since, limit=limit)
