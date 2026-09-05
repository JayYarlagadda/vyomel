"""Hard delete of entities and semantic rows (FR-509)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from vyomel.core.errors import NotFoundError
from vyomel.memory.graph import forget_entity, get_entity
from vyomel.memory.ingest import ingest_paths
from vyomel.models.embeddings import HashingEmbedder
from vyomel.store.db import session_scope
from vyomel.store.models import Document, DocumentChunk, Entity


@pytest.mark.integration
@pytest.mark.req("FR-509")
async def test_forget_removes_entity_documents_and_chunks(memory_db, tmp_path: Path) -> None:
    notes = tmp_path / "secret.txt"
    notes.write_text("TOPSECRETTOKEN must disappear on forget.\n", encoding="utf-8")

    async with session_scope() as session:
        report = await ingest_paths(session, [str(notes)], [tmp_path], embedder=HashingEmbedder())
    async with session_scope() as session:
        document = await session.get(Document, report.documents[0].document_id)
        assert document is not None
        entity_id = document.entity_id
        assert entity_id is not None

    async with session_scope() as session:
        deleted = await forget_entity(session, entity_id)
    assert deleted.documents_deleted == 1
    assert deleted.chunks_deleted >= 1

    async with session_scope() as session:
        assert await session.get(Entity, entity_id) is None
        assert (await session.scalar(select(func.count()).select_from(Document))) == 0
        assert (await session.scalar(select(func.count()).select_from(DocumentChunk))) == 0

    with pytest.raises(NotFoundError):
        async with session_scope() as session:
            await get_entity(session, entity_id)
