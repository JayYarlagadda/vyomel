"""Liveness, readiness, version, and metrics endpoints.

Liveness answers "is the process alive"; readiness answers "can it actually do
work". They are separate because Kubernetes restarts on the first and removes
from load balancing on the second -- conflating them causes restart loops when
a dependency is briefly unavailable.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST
from redis.asyncio import Redis
from sqlalchemy import text

from vyomel import __version__
from vyomel.api.schemas import HealthResponse, ReadinessCheck, ReadinessResponse, VersionResponse
from vyomel.core.config import get_settings
from vyomel.obs.metrics import exposition
from vyomel.store.db import get_engine

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get("/readyz", response_model=ReadinessResponse)
async def readyz(response: Response) -> ReadinessResponse:
    checks = [await _check_postgres(), await _check_redis()]
    ready = all(check.ok for check in checks)
    if not ready:
        response.status_code = 503
    return ReadinessResponse(ready=ready, checks=checks)


@router.get("/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    return VersionResponse(
        version=__version__,
        schema_revision=await _schema_revision(),
        environment=get_settings().env,
    )


@router.get("/metrics")
async def metrics() -> Response:
    return Response(content=exposition(), media_type=CONTENT_TYPE_LATEST)


async def _check_postgres() -> ReadinessCheck:
    started = time.perf_counter()
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        return ReadinessCheck(name="postgres", ok=False, detail=str(exc))
    return ReadinessCheck(
        name="postgres", ok=True, latency_ms=round((time.perf_counter() - started) * 1000, 2)
    )


async def _check_redis() -> ReadinessCheck:
    started = time.perf_counter()
    client: Redis | None = None
    try:
        client = Redis.from_url(get_settings().redis_url)
        await client.ping()
    except Exception as exc:
        return ReadinessCheck(name="redis", ok=False, detail=str(exc))
    finally:
        if client is not None:
            await client.aclose()
    return ReadinessCheck(
        name="redis", ok=True, latency_ms=round((time.perf_counter() - started) * 1000, 2)
    )


async def _schema_revision() -> str | None:
    try:
        async with get_engine().connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            row = result.first()
            return str(row[0]) if row else None
    except Exception:
        return None
