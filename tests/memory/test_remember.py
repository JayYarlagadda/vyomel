"""Explicit remember API and graph writes."""

from __future__ import annotations

import pytest

from vyomel.core.types import EntityType
from vyomel.memory.graph import get_entity, remember_entity
from vyomel.store.db import session_scope


@pytest.mark.integration
@pytest.mark.req("FR-502")
async def test_remember_creates_explicit_entity(memory_db) -> None:
    async with session_scope() as session:
        entity = await remember_entity(
            session,
            entity_type=EntityType.PREFERENCE,
            name="dark mode",
            aliases=["dark theme"],
            attributes={"value": "enabled"},
        )
        entity_id = entity.id

    async with session_scope() as session:
        loaded = await get_entity(session, entity_id)
        assert loaded.type is EntityType.PREFERENCE
        assert loaded.source == "explicit"
        assert loaded.attributes["value"] == "enabled"
