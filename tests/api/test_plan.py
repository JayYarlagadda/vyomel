"""Handwritten plan via the API (FR-107, FR-201)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.integration
@pytest.mark.req("FR-107")
@pytest.mark.req("FR-201")
async def test_create_task_with_handwritten_plan(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/tasks",
        json={
            "instruction": "list docs",
            "plan": {
                "steps": [
                    {
                        "alias": "survey",
                        "title": "List workspace",
                        "intent": "See files",
                        "actions": [
                            {
                                "alias": "ls",
                                "tool": "fs.list_dir",
                                "parameters": {"path": "D:/Astra/docs"},
                            }
                        ],
                    }
                ]
            },
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "READY"
    assert body["plan_version"] == 1
    assert body["progress"]["actions_total"] == 1

    plan = await client.get(f"/v1/tasks/{body['id']}/plan")
    assert plan.status_code == 200
    payload = plan.json()
    assert len(payload["steps"]) == 1
    assert payload["actions"][0]["tool"] == "fs.list_dir"
    assert payload["actions"][0]["status"] == "PLANNED"
