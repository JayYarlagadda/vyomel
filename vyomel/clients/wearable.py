"""Thin wearable HTTP client - same API as CLI/desktop (FR-1103).

Does not import orchestrator/security/store. Talks only over HTTP so a watch
or phone build can reuse the contract unchanged.
"""

from __future__ import annotations

from typing import Any, cast

import httpx

from vyomel.core.types import Capability, TaskOrigin


def task_create_payload(
    instruction: str,
    *,
    ceiling: Capability = Capability.L1,
    context_hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "instruction": instruction,
        "capability_ceiling": ceiling.value,
        "origin": TaskOrigin.API.value,
        "context_hints": {
            "client": "wearable",
            **(context_hints or {}),
        },
        "autostart": False,
        "dry_run": True,
    }


class WearableClient:
    """Minimal client a wearable would ship: health check, detect, gym plan, task create."""

    def __init__(
        self, base_url: str, *, token: str | None = None, timeout_s: float = 30.0
    ) -> None:
        headers: dict[str, str] = {"User-Agent": "vyomel-wearable/0.1"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout_s,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> WearableClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def healthz(self) -> dict[str, Any]:
        response = self._client.get("/healthz")
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    def detect_equipment(self, scene: str | None = None) -> dict[str, Any]:
        params = {"scene": scene} if scene else None
        response = self._client.get("/v1/perception/detect", params=params)
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    def plan_gym_session(self, **payload: Any) -> dict[str, Any]:
        response = self._client.post("/v1/perception/gym/session", json=payload)
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    def create_task(
        self,
        instruction: str,
        *,
        ceiling: Capability = Capability.L1,
        context_hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = task_create_payload(instruction, ceiling=ceiling, context_hints=context_hints)
        response = self._client.post("/v1/tasks", json=body)
        response.raise_for_status()
        return cast(dict[str, Any], response.json())
