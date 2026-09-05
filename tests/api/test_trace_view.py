"""Per-task trace timeline (FR-804)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


@pytest.mark.req("FR-804")
async def test_trace_endpoint_renders_the_installed_plan(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/tasks",
        json={
            "instruction": "grade submission 482",
            "capability_ceiling": "L1",
            "plan": {
                "steps": [
                    {
                        "alias": "report",
                        "title": "Report",
                        "intent": "finish",
                        "actions": [
                            {
                                "alias": "done",
                                "tool": "task.report",
                                "parameters": {"summary": "ok", "findings": []},
                            }
                        ],
                    }
                ]
            },
        },
    )
    assert response.status_code == 201
    created = response.json()
    assert created["trace_id"]
    assert response.headers.get("x-trace-id")

    trace = await client.get(f"/v1/tasks/{created['id']}/trace")
    assert trace.status_code == 200
    body = trace.json()
    assert body["task_id"] == created["id"]
    assert body["trace_id"] == created["trace_id"]
    assert "Report" in body["rendered"] or "report" in body["rendered"].lower()
    assert body["tree"]["children"]
    assert "task.report" in body["rendered"]


@pytest.mark.req("FR-802")
async def test_metrics_endpoint_uses_the_vyomel_registry(client: AsyncClient) -> None:
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "vyomel_tasks_total" in response.text
    assert "text/plain" in response.headers["content-type"]
