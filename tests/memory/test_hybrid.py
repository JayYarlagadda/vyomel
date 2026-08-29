"""Vector + lexical fusion (FR-503).

The hashing embedder is a shape stand-in for bge, not a quality claim. The
assertion is that a unique lexical identifier surfaces through hybrid RRF.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from astra.core.config import Settings
from astra.memory.ingest import ingest_paths
from astra.memory.retrieve import retrieve
from astra.store.db import session_scope


@pytest.mark.integration
@pytest.mark.req("FR-503")
async def test_hybrid_query_returns_the_chunk_with_the_unique_token(
    memory_db: Settings, tmp_path: Path
) -> None:
    other = tmp_path / "other.md"
    other.write_text("# Other\n\nUnrelated notes about calendars and mail.\n", encoding="utf-8")
    target = tmp_path / "orbit.md"
    target.write_text(
        "# Orbit\n\nThe ZX9QUNIQUE failover runbook lives in this file.\n",
        encoding="utf-8",
    )

    async with session_scope() as session:
        await ingest_paths(session, [str(other), str(target)], [tmp_path])

    async with session_scope() as session:
        hybrid = await retrieve(session, "ZX9QUNIQUE failover", k=10, strategy="hybrid")
        lexical = await retrieve(session, "ZX9QUNIQUE", k=10, strategy="lexical")
        vector = await retrieve(session, "ZX9QUNIQUE failover", k=10, strategy="vector")

    assert hybrid.strategy == "hybrid_rrf"
    assert any("ZX9QUNIQUE" in hit.content for hit in hybrid.results)
    assert lexical.results
    assert "ZX9QUNIQUE" in lexical.results[0].content
    assert lexical.results[0].lexical_rank == 1
    assert vector.results
    assert hybrid.results[0].citation.path.endswith("orbit.md")
