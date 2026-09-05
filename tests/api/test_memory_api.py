"""Memory HTTP surface: ingest and query."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from vyomel.core.config import Settings


@pytest.fixture
async def memory_client(memory_db: Settings) -> AsyncIterator[AsyncClient]:
    from vyomel.api.app import create_app

    app = create_app(memory_db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.mark.integration
@pytest.mark.req("FR-501")
async def test_ingest_and_query_over_http(memory_client: AsyncClient, tmp_path: Path) -> None:
    notes = tmp_path / "notes.md"
    notes.write_text("# Notes\n\nZX9QUNIQUE identifier in the corpus.\n", encoding="utf-8")

    ingested = await memory_client.post(
        "/v1/memory/ingest",
        json={"paths": [str(notes)], "recursive": False, "watch": False},
    )
    assert ingested.status_code == 200, ingested.text
    body = ingested.json()
    assert body["status"] == "completed"
    assert body["documents"][0]["status"] == "ingested"

    queried = await memory_client.post(
        "/v1/memory/query",
        json={"query": "ZX9QUNIQUE", "k": 5, "strategy": "hybrid"},
    )
    assert queried.status_code == 200, queried.text
    hits = queried.json()["results"]
    assert hits
    assert hits[0]["citation"]["path"].endswith("notes.md")
    assert hits[0]["citation"]["char_end"] > hits[0]["citation"]["char_start"]


@pytest.mark.integration
async def test_get_and_forget_entity_over_http(memory_client, tmp_path: Path) -> None:
    notes = tmp_path / "notes.md"
    notes.write_text("# Notes\n\nFORGETME token.\n", encoding="utf-8")

    ingested = await memory_client.post(
        "/v1/memory/ingest",
        json={"paths": [str(notes)], "recursive": False, "watch": False},
    )
    document_id = ingested.json()["documents"][0]["document_id"]

    from vyomel.store.db import session_scope
    from vyomel.store.models import Document

    async with session_scope() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        entity_id = document.entity_id

    shown = await memory_client.get(f"/v1/memory/entities/{entity_id}")
    assert shown.status_code == 200
    assert shown.json()["type"] == "document"

    deleted = await memory_client.delete(f"/v1/memory/entities/{entity_id}")
    assert deleted.status_code == 200
    assert deleted.json()["chunks_deleted"] >= 1

    missing = await memory_client.get(f"/v1/memory/entities/{entity_id}")
    assert missing.status_code == 404


@pytest.mark.integration
async def test_watch_is_not_implemented(memory_client: AsyncClient, tmp_path: Path) -> None:
    response = await memory_client.post(
        "/v1/memory/ingest",
        json={"paths": [str(tmp_path)], "watch": True},
    )
    assert response.status_code == 501


@pytest.mark.integration
async def test_path_outside_allowlist_is_forbidden(
    memory_client: AsyncClient, tmp_path: Path
) -> None:
    response = await memory_client.post(
        "/v1/memory/ingest",
        json={"paths": ["C:/Windows/notepad.exe"]},
    )
    assert response.status_code == 403
