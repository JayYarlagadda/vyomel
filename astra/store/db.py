"""Database engine, session factory, and unit-of-work helper."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pgvector.asyncpg import register_vector
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from astra.core.config import Settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(settings: Settings) -> AsyncEngine:
    global _engine, _session_factory
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
            echo=False,
        )
        _register_pgvector(_engine)
        _session_factory = async_sessionmaker(
            _engine, expire_on_commit=False, autoflush=False, class_=AsyncSession
        )
    return _engine


def _register_pgvector(engine: AsyncEngine) -> None:
    """asyncpg will otherwise send embeddings as text arrays and pgvector rejects them."""

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_connection: object, _connection_record: object) -> None:
        dbapi_connection.run_async(register_vector)  # type: ignore[attr-defined]


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Engine not initialized; call init_engine() first")
    return _engine


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional unit of work.

    Commits on clean exit, rolls back on any exception. Every state transition
    that must survive a crash is committed inside one of these before the
    corresponding side effect is attempted.
    """
    if _session_factory is None:
        raise RuntimeError("Engine not initialized; call init_engine() first")
    session = _session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with session_scope() as session:
        yield session
