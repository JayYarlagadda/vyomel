"""Local agent process: outbound WebSocket + host-bound tool execution (ADR-0009)."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

from vyomel.core.cancel import CancellationToken
from vyomel.core.clock import SystemClock
from vyomel.core.config import Settings
from vyomel.core.ids import new_id
from vyomel.core.logging import configure_logging, get_logger
from vyomel.core.types import ActionStatus, Capability
from vyomel.runtime.worker import _run_claimed
from vyomel.security.audit import AuditTrail
from vyomel.store.db import dispose_engine, init_engine, session_scope
from vyomel.store.repos import ActionRepo
from vyomel.tools.registry import default_registry

log = get_logger(__name__)


def _ws_url(api_base: str, *, token: str, agent_id: str) -> str:
    base = api_base.rstrip("/")
    if base.startswith("https://"):
        ws = "wss://" + base.removeprefix("https://")
    elif base.startswith("http://"):
        ws = "ws://" + base.removeprefix("http://")
    else:
        ws = "ws://" + base
    query = urlencode({"token": token, "agent_id": agent_id})
    return f"{ws}/v1/agents/local/ws?{query}"


async def _execute_job(settings: Settings, job: dict[str, Any]) -> dict[str, Any]:
    """Claim and run one action against the control-plane database."""
    action_id = str(job["action_id"])
    registry = default_registry(include_host_tools=True)
    clock = SystemClock()
    cancel = CancellationToken()
    audit = AuditTrail(clock)
    worker_id = f"local-agent-{new_id()[:8]}"
    now = clock.now()

    async with session_scope() as session:
        repo = ActionRepo(session)
        action = await repo.get(action_id)
        if action is None:
            return {"type": "result", "action_id": action_id, "ok": False, "error": "not_found"}
        claimed = await repo.cas_claim(
            action_id,
            worker_id=worker_id,
            lease_until=now + timedelta(seconds=action.timeout_s),
            now=now,
        )
        if claimed is None:
            return {
                "type": "result",
                "action_id": action_id,
                "ok": False,
                "error": "claim_failed",
            }

        await _run_claimed(
            claimed,
            session=session,
            repo=repo,
            registry=registry,
            settings=settings,
            clock=clock,
            cancel=cancel,
            audit=audit,
            actor=f"local-agent:{worker_id}",
            worker_id=worker_id,
        )
        fresh = await repo.get(action_id)
        return {
            "type": "result",
            "action_id": action_id,
            "ok": fresh is not None and fresh.status is ActionStatus.SUCCEEDED,
            "status": fresh.status.value if fresh else "missing",
        }


async def run_local_agent(
    settings: Settings,
    *,
    api_base: str,
    agent_id: str = "local",
    ceiling: Capability = Capability.L2,
) -> None:
    """Connect to the control plane and serve desktop tools until cancelled."""
    import websockets

    configure_logging(settings)
    settings.ensure_directories()
    init_engine(settings)
    token = settings.local_agent_token.get_secret_value()
    url = _ws_url(api_base, token=token, agent_id=agent_id)
    registry = default_registry(include_host_tools=True)
    desktop_tools = [n for n in registry.names() if n.startswith("desktop.")]

    try:
        async with websockets.connect(url) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "advertise",
                        "tools": desktop_tools,
                        "ceiling": ceiling.value,
                    }
                )
            )
            ack = json.loads(await ws.recv())
            if ack.get("type") != "registered":
                raise RuntimeError(f"local agent registration failed: {ack}")
            log.info("vyomel.local_agent.connected", api_base=api_base, tools=len(desktop_tools))
            while True:
                raw = await ws.recv()
                message = json.loads(raw)
                if message.get("type") == "execute":
                    result = await _execute_job(settings, message)
                    await ws.send(json.dumps(result))
    finally:
        await dispose_engine()
