"""Semantic memory ingest and query (docs/04-API-SPEC.md §4).

This slice is synchronous: POST ingest writes chunks before it returns.
``watch`` and async job polling are not implemented. Graph/entity routes wait
for FR-502.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from vyomel.api.schemas import (
    CitationOut,
    EntityDocumentOut,
    EntityListResponse,
    EntityOut,
    EntityRelationOut,
    EntitySummary,
    EpisodeListResponse,
    EpisodeOut,
    ForgetResponse,
    IngestedDocument,
    IngestRequest,
    IngestResponse,
    MemoryHit,
    MemoryQueryRequest,
    MemoryQueryResponse,
    RememberRequest,
    RememberResponse,
)
from vyomel.core.ids import new_id
from vyomel.core.types import EntityType
from vyomel.orchestrator.memory import MemoryService
from vyomel.store.db import get_session

router = APIRouter(prefix="/v1/memory", tags=["memory"])


def _service(request: Request) -> MemoryService:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        from vyomel.core.config import get_settings

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


@router.get("/entities", response_model=EntityListResponse)
async def list_entities(
    session: AsyncSession = Depends(get_session),
    service: MemoryService = Depends(_service),
    entity_type: EntityType | None = Query(default=None, alias="type"),
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> EntityListResponse:
    entities = await service.list_entities(session, entity_type=entity_type, query=q, limit=limit)
    return EntityListResponse(
        items=[
            EntitySummary(
                id=entity.id,
                type=entity.type.value,
                name=entity.name,
                salience=entity.salience,
                source=entity.source,
            )
            for entity in entities
        ]
    )


@router.post("/remember", response_model=RememberResponse)
async def remember(
    body: RememberRequest,
    session: AsyncSession = Depends(get_session),
    service: MemoryService = Depends(_service),
) -> RememberResponse:
    entity = await service.remember(
        session,
        entity_type=body.type,
        name=body.name,
        aliases=body.aliases,
        attributes=body.attributes,
    )
    return RememberResponse(entity_id=entity.id, type=entity.type.value, name=entity.name)


@router.get("/episodes", response_model=EpisodeListResponse)
async def list_episodes(
    session: AsyncSession = Depends(get_session),
    service: MemoryService = Depends(_service),
    entity_id: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> EpisodeListResponse:
    episodes = await service.fetch_episodes(session, entity_id=entity_id, since=since, limit=limit)
    return EpisodeListResponse(
        items=[
            EpisodeOut(
                id=episode.id,
                task_id=episode.task_id,
                summary=episode.summary,
                outcome=episode.outcome,
                entity_ids=list(episode.entity_ids),
                tools_used=list(episode.tools_used),
                started_at=episode.started_at,
                finished_at=episode.finished_at,
            )
            for episode in episodes
        ]
    )


@router.get("/entities/{entity_id}", response_model=EntityOut)
async def get_entity(
    entity_id: str,
    session: AsyncSession = Depends(get_session),
    service: MemoryService = Depends(_service),
) -> EntityOut:
    entity = await service.fetch_entity(session, entity_id)
    relations: list[EntityRelationOut] = []
    for edge in entity.outgoing_relations:
        relations.append(
            EntityRelationOut(
                id=edge.id,
                relation=edge.relation.value,
                peer_id=edge.to_id,
                peer_name=edge.to_entity.name,
                peer_type=edge.to_entity.type.value,
                direction="outgoing",
                confidence=edge.confidence,
            )
        )
    for edge in entity.incoming_relations:
        relations.append(
            EntityRelationOut(
                id=edge.id,
                relation=edge.relation.value,
                peer_id=edge.from_id,
                peer_name=edge.from_entity.name,
                peer_type=edge.from_entity.type.value,
                direction="incoming",
                confidence=edge.confidence,
            )
        )
    return EntityOut(
        id=entity.id,
        type=entity.type.value,
        name=entity.name,
        aliases=list(entity.aliases),
        attributes=dict(entity.attributes),
        salience=entity.salience,
        source=entity.source,
        first_seen_at=entity.first_seen_at,
        last_seen_at=entity.last_seen_at,
        documents=[
            EntityDocumentOut(
                id=document.id,
                path=document.path,
                chunk_count=len(document.chunks),
                version=document.version,
            )
            for document in entity.documents
        ],
        relations=relations,
    )


@router.delete("/entities/{entity_id}", response_model=ForgetResponse)
async def forget_entity(
    entity_id: str,
    session: AsyncSession = Depends(get_session),
    service: MemoryService = Depends(_service),
) -> ForgetResponse:
    report = await service.forget(session, entity_id)
    return ForgetResponse(
        entity_id=report.entity_id,
        documents_deleted=report.documents_deleted,
        chunks_deleted=report.chunks_deleted,
        relations_deleted=report.relations_deleted,
        episodes_deleted=report.episodes_deleted,
    )
