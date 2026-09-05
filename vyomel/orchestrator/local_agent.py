"""Local-agent hub for host-bound desktop tools (ADR-0009 / M13).

In-cluster workers never touch the user's screen. A laptop-side ``vyomel agent``
opens an outbound WebSocket, advertises ``desktop.*`` tools, and receives
execute jobs. The hub lives in the API process; the dispatcher asks it before
publishing desktop actions to Redis.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from vyomel.core.logging import get_logger
from vyomel.core.types import Capability

log = get_logger(__name__)


@dataclass
class AgentSession:
    agent_id: str
    tools: set[str]
    ceiling: Capability
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    results: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)


class LocalAgentHub:
    """In-memory registry of connected local agents (one API replica)."""

    def __init__(self) -> None:
        self._sessions: dict[str, AgentSession] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        agent_id: str,
        *,
        tools: list[str],
        ceiling: Capability,
    ) -> AgentSession:
        async with self._lock:
            session = AgentSession(
                agent_id=agent_id,
                tools=set(tools),
                ceiling=ceiling,
            )
            self._sessions[agent_id] = session
            log.info(
                "vyomel.local_agent.registered",
                agent_id=agent_id,
                tools=sorted(tools),
                ceiling=ceiling.value,
            )
            return session

    async def unregister(self, agent_id: str) -> None:
        async with self._lock:
            self._sessions.pop(agent_id, None)
            log.info("vyomel.local_agent.unregistered", agent_id=agent_id)

    def list_agents(self) -> list[dict[str, Any]]:
        return [
            {
                "agent_id": s.agent_id,
                "tools": sorted(s.tools),
                "ceiling": s.ceiling.value,
            }
            for s in self._sessions.values()
        ]

    def has_handler_for(self, tool: str) -> bool:
        return any(tool in s.tools for s in self._sessions.values())

    async def try_dispatch(
        self,
        action_id: str,
        *,
        tool: str,
        parameters: dict[str, Any],
        capability_level: Capability,
        traceparent: str | None = None,
    ) -> bool:
        """Offer a job to a connected agent that advertises ``tool``.

        Returns True if a session accepted the job (caller must not Redis-publish).
        """
        session = next((s for s in self._sessions.values() if tool in s.tools), None)
        if session is None:
            return False
        if capability_level > session.ceiling:
            log.warning(
                "vyomel.local_agent.ceiling_exceeded",
                agent_id=session.agent_id,
                tool=tool,
                level=capability_level.value,
                ceiling=session.ceiling.value,
            )
            return False
        job = {
            "type": "execute",
            "action_id": action_id,
            "tool": tool,
            "parameters": parameters,
            "capability_level": capability_level.value,
            "traceparent": traceparent,
        }
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        session.results[action_id] = fut
        await session.queue.put(job)
        log.info(
            "vyomel.local_agent.dispatched",
            agent_id=session.agent_id,
            action_id=action_id,
            tool=tool,
        )
        return True

    async def complete(self, agent_id: str, action_id: str, result: dict[str, Any]) -> None:
        session = self._sessions.get(agent_id)
        if session is None:
            return
        fut = session.results.pop(action_id, None)
        if fut is not None and not fut.done():
            fut.set_result(result)


_HUB: LocalAgentHub | None = None


def get_local_agent_hub() -> LocalAgentHub:
    global _HUB
    if _HUB is None:
        _HUB = LocalAgentHub()
    return _HUB


def reset_local_agent_hub(hub: LocalAgentHub | None = None) -> None:
    global _HUB
    _HUB = hub
