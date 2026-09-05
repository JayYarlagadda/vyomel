"""Wearable client uses the same HTTP API (FR-1103)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from vyomel.api.app import create_app
from vyomel.clients.wearable import WearableClient, task_create_payload
from vyomel.core.config import Settings
from vyomel.core.types import Capability, TaskOrigin


class _StarletteTransport(httpx.BaseTransport):
    def __init__(self, app: Any) -> None:
        self._test = TestClient(app)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.raw_path.decode("ascii")
        response = self._test.request(
            request.method,
            path,
            headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
            content=request.content,
        )
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=response.content,
            request=request,
        )


@pytest.mark.req("FR-1103")
def test_wearable_client_hits_perception_routes(tmp_path: Path) -> None:
    settings = Settings(
        env="test",
        workspace_root=tmp_path / ".vyomel",
        embed_scheduler=False,
    )
    settings.ensure_directories()
    app = create_app(settings)
    transport = _StarletteTransport(app)
    http = httpx.Client(transport=transport, base_url="http://test")
    wearable = WearableClient.__new__(WearableClient)
    wearable._client = http

    health = wearable.healthz()
    assert isinstance(health, dict)

    detected = wearable.detect_equipment()
    assert "barbell" in detected["equipment"]

    plan = wearable.plan_gym_session(minutes=45, recent=["bench press"])
    assert plan["blocks"]
    assert plan["equipment_used"]
    http.close()


@pytest.mark.req("FR-1103")
def test_wearable_task_payload_uses_shared_api_contract() -> None:
    body = task_create_payload(
        "I'm at the gym",
        ceiling=Capability.L1,
        context_hints={"scene": "gym_floor"},
    )
    assert body["origin"] == TaskOrigin.API.value
    assert body["context_hints"]["client"] == "wearable"
    assert body["context_hints"]["scene"] == "gym_floor"
    assert body["dry_run"] is True
    assert body["autostart"] is False


@pytest.mark.req("FR-1103")
def test_wearable_sets_client_user_agent() -> None:
    wearable = WearableClient("http://example.invalid", token="secret")
    assert wearable._client.headers["User-Agent"].startswith("vyomel-wearable")
    assert wearable._client.headers["Authorization"] == "Bearer secret"
    wearable.close()
