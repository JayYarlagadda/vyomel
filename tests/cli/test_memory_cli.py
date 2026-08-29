from __future__ import annotations

from pathlib import Path

from tests.cli.conftest import Install
from typer.testing import CliRunner

from astra.cli.main import app

runner = CliRunner()


def test_memory_ingest_posts_paths(recorder: Install) -> None:
    rec = recorder(
        {
            ("POST", "/v1/memory/ingest"): {
                "job_id": "01JOB",
                "status": "completed",
                "documents": [
                    {
                        "path": "D:/notes.md",
                        "status": "ingested",
                        "document_id": "01DOC",
                        "chunk_count": 2,
                        "version": 1,
                        "content_hash": "abc",
                    }
                ],
            }
        }
    )

    result = runner.invoke(app, ["memory", "ingest", "D:/notes.md"])

    assert result.exit_code == 0, result.output
    assert rec.calls[0]["json"]["paths"] == [str(Path("D:/notes.md"))]
    assert rec.calls[0]["json"]["recursive"] is False
    assert "ingested" in result.output


def test_memory_query_posts_hybrid(recorder: Install) -> None:
    rec = recorder(
        {
            ("POST", "/v1/memory/query"): {
                "results": [
                    {
                        "chunk_id": "01CHK",
                        "content": "hello",
                        "score": 0.5,
                        "vector_rank": 1,
                        "lexical_rank": 2,
                        "citation": {
                            "path": "D:/notes.md",
                            "heading_path": ["Design"],
                            "page": None,
                            "char_start": 0,
                            "char_end": 5,
                            "ingested_at": "2026-08-29T00:00:00Z",
                        },
                    }
                ],
                "strategy": "hybrid_rrf",
                "latency_ms": 12.0,
            }
        }
    )

    result = runner.invoke(app, ["memory", "query", "hello", "--k", "5", "--strategy", "hybrid"])

    assert result.exit_code == 0, result.output
    assert rec.calls[0]["json"] == {"query": "hello", "k": 5, "strategy": "hybrid"}
    assert "D:/notes.md" in result.output
