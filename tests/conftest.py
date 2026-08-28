"""Shared test fixtures.

Integration tests run against real Postgres and Redis rather than mocks. The
bugs that matter in this system live in transaction semantics, row locking, and
stream acknowledgement, and a mock cannot express any of them.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from astra.core.config import Settings


@pytest.fixture(scope="session")
def settings() -> Settings:
    os.environ.setdefault("ASTRA_ENV", "test")
    return Settings(env="test", log_format="json")


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    from astra.api.app import create_app

    app = create_app(settings)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as async_client:
        # ASGITransport does not trigger lifespan, so the engine is initialized here.
        from astra.store.db import dispose_engine, init_engine

        init_engine(settings)
        try:
            yield async_client
        finally:
            await dispose_engine()
