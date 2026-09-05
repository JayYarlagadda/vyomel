"""Tool catalog and debug invoke (docs/04-API-SPEC.md §5).

Listing is the catalog the planner will consume in M5. Invoke is an operator
path: it still classifies, evaluates policy, and audits, and it refuses
anything the worker would have stopped for consent.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from vyomel.api.schemas import (
    InvokeToolRequest,
    InvokeToolResponse,
    ToolCatalogItem,
    ToolListResponse,
)
from vyomel.core.config import Settings, get_settings
from vyomel.orchestrator.runtime import get_registry
from vyomel.orchestrator.tools import CatalogEntry, DirectInvoker, InvokeResult, ToolCatalog
from vyomel.store.db import get_session

router = APIRouter(prefix="/v1/tools", tags=["tools"])


def _catalog() -> ToolCatalog:
    return ToolCatalog(get_registry())


def _item(entry: CatalogEntry) -> ToolCatalogItem:
    return ToolCatalogItem(
        name=entry.name,
        version=entry.version,
        description=entry.description,
        base_capability=entry.base_capability,
        reversible=entry.reversible,
        idempotent=entry.idempotent,
        actuation_tier=entry.actuation_tier,
        concurrency_key=entry.concurrency_key,
        input_schema=entry.input_schema,
        output_schema=entry.output_schema,
    )


def _invoke_body(result: InvokeResult) -> InvokeToolResponse:
    return InvokeToolResponse(
        invoke_id=result.invoke_id,
        tool=result.tool,
        capability_level=result.capability_level,
        decision=result.decision,
        result=result.result,
    )


@router.get("", response_model=ToolListResponse)
async def list_tools() -> ToolListResponse:
    return ToolListResponse(items=[_item(entry) for entry in _catalog().list()])


@router.post("/{name}/invoke", response_model=InvokeToolResponse)
async def invoke_tool(
    name: str,
    payload: InvokeToolRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> InvokeToolResponse:
    invoker = DirectInvoker(session, settings, get_registry())
    return _invoke_body(await invoker.invoke(name, payload.parameters))


@router.get("/{name}", response_model=ToolCatalogItem)
async def get_tool(name: str) -> ToolCatalogItem:
    return _item(_catalog().show(name))
