"""Memory use cases: ingest and hybrid query.

The API and CLI never import ``astra.memory``. This module is the seam that
keeps ingest/retrieve behind the orchestrator layering line.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from astra.core.config import Settings
from astra.core.errors import AstraError, ErrorCode
from astra.memory.ingest import IngestReport, ingest_paths
from astra.memory.retrieve import Retrieval, retrieve
from astra.models.embeddings import Embedder, HashingEmbedder

Strategy = Literal["hybrid", "vector", "lexical"]


class MemoryService:
    def __init__(self, settings: Settings, embedder: Embedder | None = None) -> None:
        self._settings = settings
        self._embedder = embedder or HashingEmbedder()

    async def ingest(
        self,
        session: AsyncSession,
        paths: Sequence[str],
        *,
        recursive: bool = False,
        watch: bool = False,
    ) -> IngestReport:
        if watch:
            raise AstraError(
                "filesystem watch is not implemented in this slice",
                code=ErrorCode.UNSUPPORTED,
            )
        if not paths:
            raise AstraError("at least one path is required", code=ErrorCode.INVALID_PARAMETERS)
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
            raise AstraError("query must not be empty", code=ErrorCode.INVALID_PARAMETERS)
        return await retrieve(
            session,
            stripped,
            k=k,
            strategy=strategy,
            embedder=self._embedder,
        )
