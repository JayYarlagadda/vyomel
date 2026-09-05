"""NL planning via the API (FR-101, FR-102)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.integration
@pytest.mark.req("FR-101")
@pytest.mark.req("FR-102")
async def test_create_task_without_plan_uses_mock_planner(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/tasks",
        json={"instruction": "list D:/Vyomel/docs"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "READY"
    assert body["plan_version"] == 1
    assert body["progress"]["actions_total"] == 1

    plan = (await client.get(f"/v1/tasks/{body['id']}/plan")).json()
    assert plan["actions"][0]["tool"] == "fs.list_dir"


@pytest.mark.integration
@pytest.mark.req("FR-103")
async def test_dry_run_without_plan_stays_planning(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/tasks",
        json={"instruction": "list D:/Vyomel/docs", "dry_run": True},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "PLANNING"
    assert body["plan_version"] == 1
