"""Memory tools: query, graph lookup, remember, forget (docs/05-TOOL-SPEC.md §3.5)."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from vyomel.core.config import Settings, get_settings
from vyomel.core.types import Capability, EntityType
from vyomel.memory.graph import forget_entity, get_entity, remember_entity
from vyomel.memory.retrieve import retrieve
from vyomel.models.embeddings import get_embedder
from vyomel.store.db import session_scope
from vyomel.tools.base import Tool, ToolContext


class MemoryQueryInput(BaseModel):
    query: str = Field(min_length=1, max_length=4_000)
    k: int = Field(default=10, ge=1, le=40)
    strategy: str = Field(default="hybrid", pattern="^(hybrid|vector|lexical)$")


class MemoryHitOut(BaseModel):
    chunk_id: str
    content: str
    score: float
    path: str
    char_start: int
    char_end: int


class MemoryQueryOutput(BaseModel):
    results: list[MemoryHitOut]
    strategy: str
    latency_ms: float


class MemoryGetEntityInput(BaseModel):
    entity_id: str = Field(min_length=1)


class MemoryGetEntityOutput(BaseModel):
    id: str
    type: str
    name: str
    aliases: list[str]
    attributes: dict[str, Any]
    document_paths: list[str]


class MemoryRememberInput(BaseModel):
    type: EntityType
    name: str = Field(min_length=1, max_length=500)
    aliases: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)


class MemoryRememberOutput(BaseModel):
    entity_id: str
    type: str
    name: str


class MemoryForgetInput(BaseModel):
    entity_id: str = Field(min_length=1)


class MemoryForgetOutput(BaseModel):
    entity_id: str
    documents_deleted: int
    chunks_deleted: int
    relations_deleted: int
    episodes_deleted: int


def _settings(ctx: ToolContext) -> Settings:
    return ctx.settings or get_settings()


class MemoryQuery(Tool):
    name: ClassVar[str] = "memory.query"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Hybrid semantic search over ingested documents with citations."
    Input: ClassVar[type[BaseModel]] = MemoryQueryInput
    Output: ClassVar[type[BaseModel]] = MemoryQueryOutput
    base_capability: ClassVar[Capability] = Capability.L0

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, MemoryQueryInput)
        settings = _settings(ctx)
        embedder = get_embedder(settings)
        async with session_scope() as session:
            retrieval = await retrieve(
                session,
                params.query,
                k=params.k,
                strategy=params.strategy,  # type: ignore[arg-type]
                embedder=embedder,
            )
        return MemoryQueryOutput(
            results=[
                MemoryHitOut(
                    chunk_id=hit.chunk_id,
                    content=hit.content,
                    score=hit.score,
                    path=hit.citation.path,
                    char_start=hit.citation.char_start,
                    char_end=hit.citation.char_end,
                )
                for hit in retrieval.results
            ],
            strategy=retrieval.strategy,
            latency_ms=retrieval.latency_ms,
        )


class MemoryGetEntity(Tool):
    name: ClassVar[str] = "memory.get_entity"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Look up a context-graph entity and linked document paths."
    Input: ClassVar[type[BaseModel]] = MemoryGetEntityInput
    Output: ClassVar[type[BaseModel]] = MemoryGetEntityOutput
    base_capability: ClassVar[Capability] = Capability.L0

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, MemoryGetEntityInput)
        async with session_scope() as session:
            entity = await get_entity(session, params.entity_id)
        return MemoryGetEntityOutput(
            id=entity.id,
            type=entity.type.value,
            name=entity.name,
            aliases=list(entity.aliases),
            attributes=dict(entity.attributes),
            document_paths=[document.path for document in entity.documents],
        )


class MemoryRemember(Tool):
    name: ClassVar[str] = "memory.remember"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Persist an explicit fact or preference into the context graph."
    Input: ClassVar[type[BaseModel]] = MemoryRememberInput
    Output: ClassVar[type[BaseModel]] = MemoryRememberOutput
    base_capability: ClassVar[Capability] = Capability.L1

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, MemoryRememberInput)
        async with session_scope() as session:
            entity = await remember_entity(
                session,
                entity_type=params.type,
                name=params.name,
                aliases=params.aliases,
                attributes=params.attributes,
            )
        return MemoryRememberOutput(
            entity_id=entity.id,
            type=entity.type.value,
            name=entity.name,
        )


class MemoryForget(Tool):
    name: ClassVar[str] = "memory.forget"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Hard-delete an entity, its documents, chunks, and episodic links."
    Input: ClassVar[type[BaseModel]] = MemoryForgetInput
    Output: ClassVar[type[BaseModel]] = MemoryForgetOutput
    base_capability: ClassVar[Capability] = Capability.L2
    reversible: ClassVar[bool] = False

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        assert isinstance(params, MemoryForgetInput)
        return [{"type": "value_equals", "field": "entity_id", "expected": params.entity_id}]

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, MemoryForgetInput)
        async with session_scope() as session:
            report = await forget_entity(session, params.entity_id)
        return MemoryForgetOutput(
            entity_id=report.entity_id,
            documents_deleted=report.documents_deleted,
            chunks_deleted=report.chunks_deleted,
            relations_deleted=report.relations_deleted,
            episodes_deleted=report.episodes_deleted,
        )
