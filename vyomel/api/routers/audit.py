"""Audit endpoints (FR-307).

Read and verify only. There is no endpoint that writes to the trail, and no
endpoint that could ever change it — the table has a ``BEFORE UPDATE OR DELETE``
trigger, and this router does not offer a route that would try.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from vyomel.api.schemas import AuditListResponse, AuditRecordResponse, ChainReportResponse
from vyomel.security.audit import AuditTrail
from vyomel.store.db import get_session

router = APIRouter(prefix="/v1/audit", tags=["audit"])


@router.get("", response_model=AuditListResponse)
async def list_audit(
    session: Annotated[AsyncSession, Depends(get_session)],
    task_id: Annotated[str | None, Query()] = None,
    action_id: Annotated[str | None, Query()] = None,
    event_type: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> AuditListResponse:
    records = await AuditTrail().records(
        session,
        task_id=task_id,
        action_id=action_id,
        event_type=event_type,
        since=since,
        limit=limit,
    )
    return AuditListResponse(items=[AuditRecordResponse.model_validate(r) for r in records])


@router.post("/verify", response_model=ChainReportResponse)
async def verify_audit(
    session: Annotated[AsyncSession, Depends(get_session)],
    start_id: Annotated[int | None, Query(ge=1)] = None,
) -> ChainReportResponse:
    report = await AuditTrail().verify(session, start_id=start_id)
    return ChainReportResponse(
        ok=report.ok,
        rows=report.rows,
        first_divergence_id=report.first_divergence_id,
        detail=report.detail,
    )
