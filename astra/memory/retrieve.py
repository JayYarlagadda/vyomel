"""Hybrid retrieval: vector + lexical, fused with RRF (FR-503, FR-505)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from astra.memory.rrf import rrf
from astra.models.embeddings import Embedder, HashingEmbedder
from astra.store.models import Document, DocumentChunk

CANDIDATE_K = 40
RRF_K = 60
Strategy = Literal["hybrid", "vector", "lexical"]


@dataclass(frozen=True, slots=True)
class Citation:
    path: str
    heading_path: list[str]
    page: int | None
    char_start: int
    char_end: int
    ingested_at: datetime


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: str
    content: str
    score: float
    vector_rank: int | None
    lexical_rank: int | None
    citation: Citation


@dataclass(frozen=True, slots=True)
class Retrieval:
    results: tuple[RetrievedChunk, ...]
    strategy: str
    latency_ms: float


async def retrieve(
    session: AsyncSession,
    query: str,
    *,
    k: int = 10,
    strategy: Strategy = "hybrid",
    embedder: Embedder | None = None,
) -> Retrieval:
    started = perf_counter()
    encoder = embedder or HashingEmbedder()
    vector_ids: list[str] = []
    lexical_ids: list[str] = []
    if strategy in {"hybrid", "vector"}:
        vector_ids = await _vector_ranking(session, encoder.embed([query])[0], CANDIDATE_K)
    if strategy in {"hybrid", "lexical"}:
        lexical_ids = await _lexical_ranking(session, query, CANDIDATE_K)

    if strategy == "hybrid":
        fused = rrf(vector_ids, lexical_ids, k=RRF_K)
        label = "hybrid_rrf"
    elif strategy == "vector":
        fused = rrf(vector_ids, k=RRF_K)
        label = "vector"
    else:
        fused = rrf(lexical_ids, k=RRF_K)
        label = "lexical"

    vector_rank = {chunk_id: rank for rank, chunk_id in enumerate(vector_ids, start=1)}
    lexical_rank = {chunk_id: rank for rank, chunk_id in enumerate(lexical_ids, start=1)}
    top = fused[: max(k, 0)]
    hits = await _hydrate(session, [chunk_id for chunk_id, _ in top])
    results = tuple(
        RetrievedChunk(
            chunk_id=chunk_id,
            content=hits[chunk_id].content,
            score=score,
            vector_rank=vector_rank.get(chunk_id),
            lexical_rank=lexical_rank.get(chunk_id),
            citation=hits[chunk_id].citation,
        )
        for chunk_id, score in top
        if chunk_id in hits
    )
    return Retrieval(
        results=results,
        strategy=label,
        latency_ms=(perf_counter() - started) * 1000.0,
    )


async def _vector_ranking(session: AsyncSession, query_vec: list[float], limit: int) -> list[str]:
    distance = DocumentChunk.embedding.cosine_distance(query_vec)
    rows = await session.execute(select(DocumentChunk.id).order_by(distance).limit(limit))
    return list(rows.scalars().all())


async def _lexical_ranking(session: AsyncSession, query: str, limit: int) -> list[str]:
    stripped = query.strip()
    if not stripped:
        return []
    rows = await session.execute(
        text(
            """
            SELECT id
            FROM document_chunks
            WHERE tsv @@ plainto_tsquery('english', :q)
            ORDER BY ts_rank(tsv, plainto_tsquery('english', :q)) DESC
            LIMIT :limit
            """
        ),
        {"q": stripped, "limit": limit},
    )
    return [row[0] for row in rows.all()]


@dataclass(frozen=True, slots=True)
class _Hydrated:
    content: str
    citation: Citation


async def _hydrate(session: AsyncSession, ids: list[str]) -> dict[str, _Hydrated]:
    if not ids:
        return {}
    rows = await session.execute(
        select(DocumentChunk, Document)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(DocumentChunk.id.in_(ids))
    )
    out: dict[str, _Hydrated] = {}
    for chunk, document in rows.all():
        out[chunk.id] = _Hydrated(
            content=chunk.content,
            citation=Citation(
                path=document.path,
                heading_path=list(chunk.heading_path),
                page=chunk.page,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                ingested_at=document.ingested_at,
            ),
        )
    return out
