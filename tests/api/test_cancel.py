"""POST /v1/tasks/{id}/cancel."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


@pytest.mark.req("FR-209")
async def test_cancel_a_ready_task(client: AsyncClient) -> None:
    created = await client.post("/v1/tasks", json={"instruction": "stop me"})
    assert created.status_code == 201
    task_id = created.json()["id"]

    response = await client.post(f"/v1/tasks/{task_id}/cancel", json={"compensate": True})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["task_id"] == task_id
    assert body["status"] == "CANCELLED"
    assert body["compensated"] == []
    assert body["irreversible"] == []

    fetched = await client.get(f"/v1/tasks/{task_id}")
    assert fetched.json()["status"] == "CANCELLED"


@pytest.mark.req("FR-209")
async def test_cancel_is_idempotent(client: AsyncClient) -> None:
    created = await client.post("/v1/tasks", json={"instruction": "stop me twice"})
    task_id = created.json()["id"]
    first = await client.post(f"/v1/tasks/{task_id}/cancel", json={"compensate": True})
    second = await client.post(f"/v1/tasks/{task_id}/cancel", json={"compensate": True})
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "CANCELLED"


async def test_cancel_unknown_task_is_404(client: AsyncClient) -> None:
    response = await client.post("/v1/tasks/01NOPE/cancel", json={"compensate": True})
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
