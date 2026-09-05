"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from vyomel import __version__
from vyomel.api.routers import (
    agents,
    approvals,
    audit,
    health,
    memory,
    perception,
    policy,
    tasks,
    tools,
    voice,
    workflows,
)
from vyomel.core.config import Settings, get_settings
from vyomel.core.errors import VyomelError
from vyomel.core.logging import configure_logging, get_logger
from vyomel.obs.tracing import current_trace_id, parse_traceparent, start_span
from vyomel.orchestrator.runtime import SchedulerHandle
from vyomel.store.db import dispose_engine, init_engine

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        resolved.ensure_directories()
        init_engine(resolved)
        handle = SchedulerHandle(resolved)
        if resolved.env != "test" and resolved.embed_scheduler:
            await handle.start()
        log.info("vyomel.api.started", version=__version__, env=resolved.env)
        try:
            yield
        finally:
            if resolved.env != "test" and resolved.embed_scheduler:
                await handle.stop()
            await dispose_engine()
            log.info("vyomel.api.stopped")

    app = FastAPI(
        title="Vyomel",
        version=__version__,
        summary="Personal AI execution platform",
        lifespan=lifespan,
    )
    app.state.settings = resolved

    app.include_router(health.router)
    app.include_router(tasks.router)
    app.include_router(approvals.router)
    app.include_router(audit.router)
    app.include_router(policy.router)
    app.include_router(tools.router)
    app.include_router(memory.router)
    app.include_router(agents.router)
    app.include_router(workflows.router)
    app.include_router(voice.router)
    app.include_router(perception.router)

    @app.middleware("http")
    async def _trace_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("traceparent")
        parsed = parse_traceparent(incoming)
        tid = parsed[0] if parsed else None
        with start_span("http.request", trace_id=tid) as span:
            span.set(**{"http.method": request.method, "http.route": request.url.path})
            response = await call_next(request)
            trace_id = current_trace_id()
            if trace_id:
                response.headers["X-Trace-Id"] = trace_id
            return response

    @app.exception_handler(VyomelError)
    async def _vyomel_error_handler(request: Request, exc: VyomelError) -> JSONResponse:
        log.warning("vyomel.error", code=exc.code, path=request.url.path, detail=exc.detail)
        return JSONResponse(status_code=exc.http_status, content=exc.to_problem())

    return app


app = create_app()
