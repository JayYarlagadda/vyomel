"""Incremental re-ingest on content change (FR-506)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from vyomel.core.config import Settings
from vyomel.memory.ingest import ingest_paths
from vyomel.store.db import session_scope
from vyomel.store.models import DocumentChunk


@pytest.mark.integration
@pytest.mark.req("FR-506")
async def test_changed_bytes_replace_chunks_and_bump_version(
    memory_db: Settings, tmp_path: Path
) -> None:
    path = tmp_path / "notes.md"
    path.write_text("version one unique_alpha", encoding="utf-8")

    async with session_scope() as session:
        first = await ingest_paths(session, [str(path)], [tmp_path])

    path.write_text("version two unique_beta " + ("word " * 20), encoding="utf-8")

    async with session_scope() as session:
        second = await ingest_paths(session, [str(path)], [tmp_path])

    assert second.documents[0].status == "replaced"
    assert second.documents[0].version == 2
    assert second.documents[0].content_hash != first.documents[0].content_hash

    async with session_scope() as session:
        count = await session.scalar(select(func.count()).select_from(DocumentChunk))
    assert count == second.documents[0].chunk_count
