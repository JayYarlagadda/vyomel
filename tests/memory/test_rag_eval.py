"""Smoke test for the RAG eval harness on the starter corpus."""

from __future__ import annotations

from pathlib import Path

import pytest
from evals.suites.rag.recall import CORPUS, run_eval

from astra.core.config import Settings


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.timeout(300)
async def test_starter_corpus_recall_with_hashing(tmp_path: Path) -> None:
    corpus_parent = CORPUS.parent
    settings = Settings(
        env="test",
        embedding_backend="hashing",
        allowed_roots=[corpus_parent],
        workspace_root=tmp_path / ".astra-eval",
    )
    settings.ensure_directories()
    score = await run_eval(settings, k=10)
    assert score >= 0.85
