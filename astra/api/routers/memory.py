"""Semantic memory ingest and query (docs/04-API-SPEC.md §4).

This slice is synchronous: POST ingest writes chunks before it returns.
``watch`` and async job polling are not implemented. Graph/entity routes wait
for FR-502.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from astra.api.schemas import (
    CitationOut,
    IngestedDocument,
    IngestRequest,
    IngestResponse,
    MemoryHit,
    MemoryQueryRequest,
    MemoryQueryResponse,
)
from astra.core.ids import new_id
from astra.orchestrator.memory import MemoryService
from astra.store.db import get_session

router = APIRouter(prefix="/v1/memory", tags=["memory"])


def _service(request: Request) -> MemoryService:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        from astra.core.config import get_settings

        settings = get_settings()
    return MemoryService(settings)


@router.post("/ingest", response_model=IngestResponse)
async def ingest(
    body: IngestRequest,
    session: AsyncSession = Depends(get_session),
    service: MemoryService = Depends(_service),
) -> IngestResponse:
    report = await service.ingest(session, body.paths, recursive=body.recursive, watch=body.watch)
    return IngestResponse(
        job_id=new_id(),
        documents=[
            IngestedDocument(
                path=item.path,
                status=item.status,
                document_id=item.document_id,
                chunk_count=item.chunk_count,
                version=item.version,
                content_hash=item.content_hash,
            )
            for item in report.documents
        ],
    )


@router.post("/query", response_model=MemoryQueryResponse)
async def query(
    body: MemoryQueryRequest,
    session: AsyncSession = Depends(get_session),
    service: MemoryService = Depends(_service),
) -> MemoryQueryResponse:
    retrieval = await service.query(session, body.query, k=body.k, strategy=body.strategy)
    return MemoryQueryResponse(
        results=[
            MemoryHit(
                chunk_id=hit.chunk_id,
                content=hit.content,
                score=hit.score,
                vector_rank=hit.vector_rank,
                lexical_rank=hit.lexical_rank,
                citation=CitationOut(
                    path=hit.citation.path,
                    heading_path=hit.citation.heading_path,
                    page=hit.citation.page,
                    char_start=hit.citation.char_start,
                    char_end=hit.citation.char_end,
                    ingested_at=hit.citation.ingested_at,
                ),
            )
            for hit in retrieval.results
        ],
        strategy=retrieval.strategy,
        latency_ms=retrieval.latency_ms,
    )
