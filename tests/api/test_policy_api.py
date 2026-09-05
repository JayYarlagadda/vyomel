"""Policy inspection over HTTP.

``/v1/policy/test`` is the endpoint an operator uses to answer "why was that
denied" without reproducing the task, so it has to report the classification and
the deciding rule, not just the verdict.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient


@pytest.mark.integration
@pytest.mark.req("FR-302")
async def test_the_active_policy_is_inspectable(client: AsyncClient) -> None:
    response = await client.get("/v1/policy")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] >= 1
    assert body["policy_hash"]
    assert body["rules"], "the shipped policy is not empty"
    assert body["egress_deny_by_default"] is True
    assert body["defaults"]["L4"] in {"CONFIRM", "DENY"}


@pytest.mark.integration
@pytest.mark.req("FR-302")
async def test_reload_reports_the_same_policy_when_the_file_is_unchanged(
    client: AsyncClient,
) -> None:
    before = (await client.get("/v1/policy")).json()
    after = (await client.post("/v1/policy/reload")).json()
    assert after["policy_hash"] == before["policy_hash"]


@pytest.mark.integration
@pytest.mark.req("FR-301")
async def test_test_endpoint_classifies_and_decides(client: AsyncClient, tmp_path: Path) -> None:
    target = tmp_path / "notes.md"
    target.write_text("hello", encoding="utf-8")

    response = await client.post(
        "/v1/policy/test",
        json={"tool": "fs.read_file", "parameters": {"path": str(target)}},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tool"] == "fs.read_file"
    assert body["capability_level"] == "L0"
    assert body["decision"] in {"ALLOW", "CONFIRM", "DENY"}
    assert body["rule_id"]
    assert body["policy_hash"]


@pytest.mark.integration
@pytest.mark.req("FR-301")
async def test_test_endpoint_reports_escalation_on_a_credential_path(
    client: AsyncClient, tmp_path: Path
) -> None:
    secret = tmp_path / ".env"
    secret.write_text("VYOMEL_API_TOKEN=nope", encoding="utf-8")

    body = (
        await client.post(
            "/v1/policy/test",
            json={"tool": "fs.read_file", "parameters": {"path": str(secret)}},
        )
    ).json()

    assert body["capability_level"] == "L4"
    assert body["escalation_reasons"]
    assert body["decision"] == "DENY"


@pytest.mark.integration
async def test_test_endpoint_rejects_an_unknown_tool_and_bad_parameters(
    client: AsyncClient,
) -> None:
    unknown = await client.post("/v1/policy/test", json={"tool": "nope.nothing"})
    assert unknown.status_code == 404, unknown.text

    invalid = await client.post("/v1/policy/test", json={"tool": "fs.read_file", "parameters": {}})
    assert invalid.status_code == 422, invalid.text
    assert invalid.json()["code"] == "INVALID_PARAMETERS"
