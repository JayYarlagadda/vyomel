"""Context graph entity lifecycle (FR-502)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from astra.core.types import EntityType
from astra.memory.graph import get_entity
from astra.memory.ingest import ingest_paths
from astra.models.embeddings import HashingEmbedder
from astra.store.db import session_scope
from astra.store.models import Document, Entity


@pytest.mark.integration
@pytest.mark.req("FR-502")
async def test_ingest_creates_document_entity(memory_db, tmp_path: Path) -> None:
    notes = tmp_path / "orbit.md"
    notes.write_text("# Orbit\n\nGateway retry policy is exponential.\n", encoding="utf-8")

    async with session_scope() as session:
        report = await ingest_paths(session, [str(notes)], [tmp_path], embedder=HashingEmbedder())

    async with session_scope() as session:
        document = await session.get(Document, report.documents[0].document_id)
        assert document is not None
        assert document.entity_id is not None
        entity = await get_entity(session, document.entity_id)
        assert entity.type == EntityType.DOCUMENT
        assert entity.name == "orbit"
        assert "orbit.md" in entity.aliases
        assert entity.documents[0].path.endswith("orbit.md")


@pytest.mark.integration
@pytest.mark.req("FR-502")
async def test_skipped_reingest_refreshes_entity(memory_db, tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("stable", encoding="utf-8")

    async with session_scope() as session:
        first = await ingest_paths(session, [str(path)], [tmp_path], embedder=HashingEmbedder())
    async with session_scope() as session:
        document = await session.get(Document, first.documents[0].document_id)
        assert document is not None
        entity_id = document.entity_id
        before = await get_entity(session, entity_id)
    async with session_scope() as session:
        await ingest_paths(session, [str(path)], [tmp_path], embedder=HashingEmbedder())
    async with session_scope() as session:
        after = await get_entity(session, entity_id)
        assert after.last_seen_at >= before.last_seen_at

    async with session_scope() as session:
        count = await session.scalar(select(func.count()).select_from(Entity))
        assert count == 1
