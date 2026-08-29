"""Shared test fixtures.

Integration tests run against real Postgres and Redis rather than mocks. The
bugs that matter in this system live in transaction semantics, row locking, and
stream acknowledgement, and a mock cannot express any of them.

The runtime fixtures live here rather than under ``tests/runtime`` because the
security suite drives the same scheduler and worker: an approval gate is only
meaningfully tested against the thing it gates.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import delete

from astra.core.config import Settings
from astra.core.ids import new_id
from astra.orchestrator.runtime import make_scheduler, make_worker, reset_registry
from astra.runtime.queue import ActionQueue
from astra.runtime.scheduler import Scheduler
from astra.runtime.worker import Worker
from astra.store.db import dispose_engine, init_engine, session_scope
from astra.store.models import Document, Task
from tests.fakes import registry_with_fakes


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


@pytest.fixture(autouse=True)
def tool_registry() -> Iterator[None]:
    """Autouse so it is installed before any fixture that captures the registry.

    ``make_worker``/``make_scheduler`` read the process registry at construction
    time, so a non-autouse fixture would only take effect if every test listed
    it before ``worker``.
    """
    reset_registry(registry_with_fakes())
    yield
    reset_registry(None)


@pytest.fixture
def runtime_settings(tmp_path: Path) -> Settings:
    return Settings(
        env="test",
        log_format="json",
        allowed_roots=[tmp_path],
        workspace_root=tmp_path / ".astra",
        max_parallel_actions=4,
        max_retries=2,
        action_timeout_s=15,
        cancel_grace_s=0.5,
    )


@pytest.fixture
async def runtime_db(runtime_settings: Settings) -> AsyncIterator[Settings]:
    """Isolated database state.

    ``Scheduler.tick`` queries *every* runnable task, so a task left behind by
    an earlier test is dispatched into this test's stream and its worker claims
    it. Truncating on both sides of the test makes the suite order-independent
    instead of accidentally coupled through the tasks table.

    ``audit_log`` is deliberately not truncated: it has no foreign key to tasks
    and is append-only by design, so the chain accumulates across the suite —
    which is itself a useful assertion that concurrent appends never fork it.
    """
    init_engine(runtime_settings)
    runtime_settings.ensure_directories()
    await _truncate_tasks()
    try:
        yield runtime_settings
    finally:
        await _truncate_tasks()
        await dispose_engine()


async def _truncate_tasks() -> None:
    # Steps, actions, approvals, ledger rows, and dead letters cascade from tasks.
    async with session_scope() as session:
        await session.execute(delete(Task))


@pytest.fixture
async def memory_db(runtime_db: Settings) -> AsyncIterator[Settings]:
    """Same isolation as ``runtime_db``, plus empty document tables."""
    await _truncate_documents()
    try:
        yield runtime_db
    finally:
        await _truncate_documents()


async def _truncate_documents() -> None:
    async with session_scope() as session:
        await session.execute(delete(Document))


@pytest.fixture
async def queue(runtime_settings: Settings) -> AsyncIterator[ActionQueue]:
    client = Redis.from_url(runtime_settings.redis_url, decode_responses=True)
    q = ActionQueue(client, stream=f"astra:test:{new_id()}", group="workers")
    await q.ensure_group()
    try:
        yield q
    finally:
        await client.delete(q.stream)
        await client.aclose()


@pytest.fixture
def scheduler(runtime_db: Settings, queue: ActionQueue) -> Scheduler:
    return make_scheduler(runtime_db, queue)


@pytest.fixture
def worker(runtime_db: Settings, queue: ActionQueue) -> Worker:
    return make_worker(runtime_db, queue, worker_id="test-worker")
