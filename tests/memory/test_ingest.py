"""Ingest md/txt, keyed on content hash (FR-501, FR-504, FR-506)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from vyomel.core.config import Settings
from vyomel.memory.ingest import ingest_paths
from vyomel.models.embeddings import HashingEmbedder
from vyomel.store.db import session_scope
from vyomel.store.models import Document, DocumentChunk


@pytest.mark.integration
@pytest.mark.req("FR-501")
async def test_markdown_file_becomes_chunks_with_embeddings(
    memory_db: Settings, tmp_path: Path
) -> None:
    notes = tmp_path / "orbit.md"
    notes.write_text("# Orbit\n\nThe gateway uses gRPC.\n", encoding="utf-8")

    async with session_scope() as session:
        report = await ingest_paths(session, [str(notes)], [tmp_path], embedder=HashingEmbedder())

    assert len(report.documents) == 1
    item = report.documents[0]
    assert item.status == "ingested"
    assert item.chunk_count >= 1

    async with session_scope() as session:
        document = await session.get(Document, item.document_id)
        assert document is not None
        assert document.mime == "text/markdown"
        chunks = (
            (
                await session.execute(
                    select(DocumentChunk).where(DocumentChunk.document_id == document.id)
                )
            )
            .scalars()
            .all()
        )
        assert chunks
        assert all(len(chunk.embedding) == 384 for chunk in chunks)
        assert all(chunk.embedding_model == HashingEmbedder().name for chunk in chunks)


@pytest.mark.integration
@pytest.mark.req("FR-504")
async def test_documents_are_not_vectors() -> None:
    assert not hasattr(Document, "embedding")
    assert hasattr(DocumentChunk, "embedding")


@pytest.mark.integration
@pytest.mark.req("FR-506")
async def test_unchanged_hash_is_a_noop(memory_db: Settings, tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("same bytes", encoding="utf-8")

    async with session_scope() as session:
        first = await ingest_paths(session, [str(path)], [tmp_path])
    async with session_scope() as session:
        second = await ingest_paths(session, [str(path)], [tmp_path])

    assert first.documents[0].status == "ingested"
    assert second.documents[0].status == "skipped"
    assert second.documents[0].document_id == first.documents[0].document_id
    assert second.documents[0].version == 1
