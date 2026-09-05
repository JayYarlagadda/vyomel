"""Local-agent WebSocket registration (ADR-0009 / M13)."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from vyomel.core.config import Settings
from vyomel.core.types import Capability
from vyomel.orchestrator.local_agent import get_local_agent_hub

router = APIRouter(tags=["agents"])


def _settings(websocket: WebSocket) -> Settings:
    return websocket.app.state.settings  # type: ignore[no-any-return]


@router.get("/v1/agents")
async def list_agents() -> JSONResponse:
    hub = get_local_agent_hub()
    return JSONResponse({"agents": hub.list_agents()})


@router.websocket("/v1/agents/local/ws")
async def local_agent_ws(
    websocket: WebSocket,
    token: str = Query(default=""),
    agent_id: str = Query(default="local"),
) -> None:
    """Outbound local-agent channel. Auth: ``?token=`` must match ``VYOMEL_LOCAL_AGENT_TOKEN``."""
    settings = _settings(websocket)
    expected = settings.local_agent_token.get_secret_value()
    if expected and token != expected:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    hub = get_local_agent_hub()
    session = None
    try:
        hello_raw = await asyncio.wait_for(websocket.receive_text(), timeout=30)
        hello = json.loads(hello_raw)
        if hello.get("type") != "advertise":
            await websocket.close(code=4400)
            return
        tools = [str(t) for t in hello.get("tools", [])]
        ceiling = Capability(str(hello.get("ceiling", "L2")))
        session = await hub.register(agent_id, tools=tools, ceiling=ceiling)
        await websocket.send_text(json.dumps({"type": "registered", "agent_id": agent_id}))

        async def _pump_jobs() -> None:
            assert session is not None
            while True:
                job = await session.queue.get()
                await websocket.send_text(json.dumps(job))

        pump = asyncio.create_task(_pump_jobs())
        try:
            while True:
                message = await websocket.receive_text()
                payload: dict[str, Any] = json.loads(message)
                if payload.get("type") == "result":
                    await hub.complete(
                        agent_id,
                        str(payload["action_id"]),
                        payload,
                    )
                elif payload.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
        finally:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
    except (WebSocketDisconnect, TimeoutError, json.JSONDecodeError, KeyError, ValueError):
        pass
    finally:
        await hub.unregister(agent_id)
