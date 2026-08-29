"""Tool catalog and debug invoke over HTTP (FR-101 adjacent, FR-602).

Invoke is the operator path in docs/04 §5: it still classifies and evaluates
policy, and it refuses anything the worker would have stopped for consent.
A 200 from invoke is therefore an ALLOW, never a skipped gate.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from astra.security.audit import AuditEvent


@pytest.mark.integration
@pytest.mark.req("FR-602")
async def test_the_catalog_lists_production_tools_with_schemas(client: AsyncClient) -> None:
    response = await client.get("/v1/tools")
    assert response.status_code == 200, response.text
    items = {item["name"]: item for item in response.json()["items"]}
    assert "fs.read_file" in items
    assert "git.push" in items
    assert items["fs.read_file"]["base_capability"] == "L0"
    assert items["git.push"]["base_capability"] == "L3"
    assert items["fs.read_file"]["input_schema"]
    assert items["git.push"]["reversible"] is False


@pytest.mark.integration
@pytest.mark.req("FR-602")
async def test_one_tool_is_retrievable_by_dotted_name(client: AsyncClient) -> None:
    response = await client.get("/v1/tools/fs.write_file")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "fs.write_file"
    assert "path" in body["input_schema"]["properties"]


@pytest.mark.integration
async def test_unknown_tool_is_not_found(client: AsyncClient) -> None:
    missing = await client.get("/v1/tools/nope.nothing")
    assert missing.status_code == 404, missing.text
    invoke = await client.post("/v1/tools/nope.nothing/invoke", json={"parameters": {}})
    assert invoke.status_code == 404, invoke.text


@pytest.mark.integration
@pytest.mark.req("FR-302")
async def test_invoke_runs_an_allowed_l0_tool_and_audits_it(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/tools/task.report/invoke",
        json={"parameters": {"summary": "hello from invoke", "findings": ["a"]}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tool"] == "task.report"
    assert body["decision"] == "ALLOW"
    assert body["result"]["summary"] == "hello from invoke"

    trail = await client.get(
        "/v1/audit", params={"action_id": body["invoke_id"], "event_type": AuditEvent.TOOL_INVOKED}
    )
    assert trail.status_code == 200
    items = trail.json()["items"]
    assert items
    assert items[0]["payload"]["tool"] == "task.report"


@pytest.mark.integration
@pytest.mark.req("FR-302")
async def test_invoke_refuses_confirm_and_does_not_run_the_tool(
    client: AsyncClient,
) -> None:
    from tests.fakes import Notify

    before = len(Notify.delivered)
    response = await client.post(
        "/v1/tools/test.notify/invoke",
        json={"parameters": {"recipient": "dean@example.edu", "body": "nope"}},
    )
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "PERMISSION_DENIED"
    assert len(Notify.delivered) == before

    trail = await client.get(
        "/v1/audit", params={"event_type": AuditEvent.POLICY_CONFIRM, "limit": 20}
    )
    assert any(
        item["payload"].get("tool") == "test.notify" and item["payload"].get("direct") is True
        for item in trail.json()["items"]
    )


@pytest.mark.integration
@pytest.mark.req("FR-301")
async def test_invoke_refuses_a_denied_credential_path(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/tools/fs.read_file/invoke",
        json={"parameters": {"path": "D:/Astra/.env"}},
    )
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "PERMISSION_DENIED"


@pytest.mark.integration
async def test_invoke_rejects_invalid_parameters(client: AsyncClient) -> None:
    response = await client.post("/v1/tools/task.report/invoke", json={"parameters": {}})
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "INVALID_PARAMETERS"
