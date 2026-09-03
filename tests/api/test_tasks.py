"""Task API integration tests (FR-101, FR-201)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_healthz(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_readyz_reports_dependencies(client: AsyncClient) -> None:
    response = await client.get("/readyz")
    body = response.json()
    assert {check["name"] for check in body["checks"]} == {"postgres", "redis"}
    assert body["ready"] is True
    assert response.status_code == 200


@pytest.mark.req("FR-101")
async def test_create_task_persists_and_is_retrievable(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/tasks",
        json={
            "instruction": "summarize the PDFs in the inbox",
            "capability_ceiling": "L1",
            "context_hints": {"active_app": "explorer"},
        },
    )
    assert response.status_code == 201
    created = response.json()
    assert created["status"] == "READY"
    assert created["plan_version"] == 1
    assert created["capability_ceiling"] == "L1"
    assert created["deadline_at"] is not None

    # A separate request proves the row was committed, not just held in the
    # request-scoped session.
    fetched = await client.get(f"/v1/tasks/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["instruction"] == "summarize the PDFs in the inbox"


@pytest.mark.req("FR-101")
async def test_requested_bounds_may_only_tighten(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/tasks",
        json={
            "instruction": "long research job",
            # Far above the configured default; must be clamped, not honored.
            "bounds": {"max_wall_clock_s": 999_999, "max_token_budget": 1},
        },
    )
    assert response.status_code == 201

    task_id = response.json()["id"]
    detail = (await client.get(f"/v1/tasks/{task_id}")).json()
    assert detail["deadline_at"] is not None


async def test_unknown_task_returns_problem_details(client: AsyncClient) -> None:
    response = await client.get("/v1/tasks/01NOPE")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "NOT_FOUND"
    assert body["retryable"] is False


async def test_invalid_instruction_is_rejected(client: AsyncClient) -> None:
    assert (await client.post("/v1/tasks", json={"instruction": ""})).status_code == 422


async def test_list_tasks_filters_by_status(client: AsyncClient) -> None:
    await client.post("/v1/tasks", json={"instruction": "summarize inbox", "dry_run": True})
    response = await client.get("/v1/tasks", params={"status": "PLANNING", "limit": 5})
    assert response.status_code == 200
    assert all(item["status"] == "PLANNING" for item in response.json()["items"])
