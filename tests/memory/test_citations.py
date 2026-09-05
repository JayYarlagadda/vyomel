"""Citations carry path and original-file offsets (FR-505)."""

from __future__ import annotations

from pathlib import Path

import pytest

from vyomel.core.config import Settings
from vyomel.memory.ingest import ingest_paths
from vyomel.memory.retrieve import retrieve
from vyomel.store.db import session_scope


@pytest.mark.integration
@pytest.mark.req("FR-505")
async def test_retrieved_span_matches_the_source_file(memory_db: Settings, tmp_path: Path) -> None:
    path = tmp_path / "design.md"
    body = "# Design\n\nRetry uses ZX9QUNIQUE token in this paragraph.\n"
    path.write_text(body, encoding="utf-8", newline="\n")

    async with session_scope() as session:
        await ingest_paths(session, [str(path)], [tmp_path])

    async with session_scope() as session:
        result = await retrieve(session, "ZX9QUNIQUE", k=5, strategy="lexical")

    assert result.results
    hit = result.results[0]
    cited = path.read_text(encoding="utf-8")[hit.citation.char_start : hit.citation.char_end]
    assert cited == hit.content
    assert hit.citation.path.endswith("design.md")
    assert "Design" in hit.citation.heading_path
    assert hit.citation.ingested_at is not None
