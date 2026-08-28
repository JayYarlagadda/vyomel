"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from astra import __version__
from astra.api.routers import health, tasks
from astra.core.config import Settings, get_settings
from astra.core.errors import AstraError
from astra.core.logging import configure_logging, get_logger
from astra.store.db import dispose_engine, init_engine

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        resolved.ensure_directories()
        init_engine(resolved)
        log.info("astra.api.started", version=__version__, env=resolved.env)
        try:
            yield
        finally:
            await dispose_engine()
            log.info("astra.api.stopped")

    app = FastAPI(
        title="Astra",
        version=__version__,
        summary="Personal AI execution platform",
        lifespan=lifespan,
    )

    app.include_router(health.router)
    app.include_router(tasks.router)

    @app.exception_handler(AstraError)
    async def _astra_error_handler(request: Request, exc: AstraError) -> JSONResponse:
        log.warning("astra.error", code=exc.code, path=request.url.path, detail=exc.detail)
        return JSONResponse(status_code=exc.http_status, content=exc.to_problem())

    return app


app = create_app()
