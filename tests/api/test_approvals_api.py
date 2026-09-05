"""The approval queue over HTTP (FR-303, FR-304, FR-305, FR-307).

The CLI and every future client reach approvals only through these endpoints, so
"the gate works" is not enough — the gate has to be *operable* from outside the
process. These tests drive the real scheduler and then answer through HTTP,
which is the path a user actually takes.

Plans are installed directly rather than posted, because what is under test here
is the approval surface, not plan installation (covered in ``test_plan.py``).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from vyomel.core.config import Settings
from vyomel.core.types import ActionStatus, ApprovalStatus, Capability
from vyomel.runtime.scheduler import Scheduler
from vyomel.runtime.worker import Worker
from vyomel.security.audit import AuditEvent
from tests.fakes import Notify
from tests.runtime.helpers import install_plan
from tests.security.test_approval_gate import notify_plan, only_action, only_approval


async def pending(client: AsyncClient, task_id: str) -> dict:
    response = await client.get("/v1/approvals", params={"task_id": task_id})
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1, items
    return items[0]


@pytest.mark.integration
@pytest.mark.req("FR-304")
async def test_the_queue_shows_a_blocked_action_with_everything_needed_to_decide(
    client: AsyncClient, runtime_db: Settings, scheduler: Scheduler
) -> None:
    task = await install_plan(runtime_db, notify_plan("dean@example.edu"), ceiling=Capability.L3)
    await scheduler.tick()

    item = await pending(client, task.id)

    assert item["status"] == ApprovalStatus.PENDING.value
    assert item["capability_level"] == "L3"
    assert item["task_id"] == task.id
    assert item["summary"]
    assert item["presented"]["parameters"]["recipient"] == "dean@example.edu"
    assert item["blast_radius"]["externally_visible"] is True
    assert item["expires_at"]

    single = await client.get(f"/v1/approvals/{item['id']}")
    assert single.status_code == 200
    assert single.json()["id"] == item["id"]


@pytest.mark.integration
@pytest.mark.req("FR-303")
async def test_approving_over_http_releases_the_action(
    client: AsyncClient, runtime_db: Settings, scheduler: Scheduler, worker: Worker
) -> None:
    Notify.delivered.clear()
    task = await install_plan(runtime_db, notify_plan("ops@example.com"), ceiling=Capability.L3)
    await scheduler.tick()
    item = await pending(client, task.id)

    decided = await client.post(
        f"/v1/approvals/{item['id']}/decide",
        json={"decision": "APPROVED", "decided_by": "user:http"},
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == ApprovalStatus.APPROVED.value
    assert decided.json()["decided_by"] == "user:http"

    assert await scheduler.tick() == 1
    assert await worker.run_once(block_ms=200)
    assert Notify.delivered == ["ops@example.com"]


@pytest.mark.integration
@pytest.mark.req("FR-303")
async def test_rejecting_over_http_fails_the_action(
    client: AsyncClient, runtime_db: Settings, scheduler: Scheduler
) -> None:
    Notify.delivered.clear()
    task = await install_plan(runtime_db, notify_plan(), ceiling=Capability.L3)
    await scheduler.tick()
    item = await pending(client, task.id)

    decided = await client.post(
        f"/v1/approvals/{item['id']}/decide",
        json={"decision": "REJECTED", "reason": "wrong recipient"},
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == ApprovalStatus.REJECTED.value

    await scheduler.tick()
    action = await only_action(task.id)
    assert action.status is ActionStatus.FAILED
    assert Notify.delivered == []


@pytest.mark.integration
@pytest.mark.req("FR-305")
async def test_a_modification_is_revalidated_and_reclassified(
    client: AsyncClient, runtime_db: Settings, scheduler: Scheduler, worker: Worker
) -> None:
    """The edited invocation is the one that runs, and it runs under a fresh
    classification rather than under the level the user was shown."""
    Notify.delivered.clear()
    task = await install_plan(runtime_db, notify_plan("wrong@example.com"), ceiling=Capability.L3)
    await scheduler.tick()
    item = await pending(client, task.id)

    decided = await client.post(
        f"/v1/approvals/{item['id']}/decide",
        json={
            "decision": "MODIFIED",
            "parameters": {"recipient": "right@example.com", "body": "done"},
        },
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == ApprovalStatus.MODIFIED.value

    await scheduler.tick()
    assert await worker.run_once(block_ms=200)
    assert Notify.delivered == ["right@example.com"]


@pytest.mark.integration
@pytest.mark.req("FR-305")
async def test_a_modification_must_satisfy_the_tool_schema(
    client: AsyncClient, runtime_db: Settings, scheduler: Scheduler
) -> None:
    task = await install_plan(runtime_db, notify_plan(), ceiling=Capability.L3)
    await scheduler.tick()
    item = await pending(client, task.id)

    refused = await client.post(
        f"/v1/approvals/{item['id']}/decide",
        json={"decision": "MODIFIED", "parameters": {"body": "no recipient at all"}},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "CONFLICT"

    approval = await only_approval(task.id)
    assert approval.status is ApprovalStatus.PENDING, "a rejected edit must not decide anything"


@pytest.mark.integration
async def test_parameters_and_decision_must_agree(
    client: AsyncClient, runtime_db: Settings, scheduler: Scheduler
) -> None:
    task = await install_plan(runtime_db, notify_plan(), ceiling=Capability.L3)
    await scheduler.tick()
    item = await pending(client, task.id)

    for payload in (
        {"decision": "MODIFIED"},
        {"decision": "APPROVED", "parameters": {"recipient": "a@b.com", "body": "x"}},
    ):
        response = await client.post(f"/v1/approvals/{item['id']}/decide", json=payload)
        assert response.status_code == 422, response.text


@pytest.mark.integration
async def test_deciding_twice_conflicts(
    client: AsyncClient, runtime_db: Settings, scheduler: Scheduler
) -> None:
    task = await install_plan(runtime_db, notify_plan(), ceiling=Capability.L3)
    await scheduler.tick()
    item = await pending(client, task.id)

    first = await client.post(f"/v1/approvals/{item['id']}/decide", json={"decision": "APPROVED"})
    assert first.status_code == 200
    second = await client.post(f"/v1/approvals/{item['id']}/decide", json={"decision": "REJECTED"})
    assert second.status_code == 409, second.text


@pytest.mark.integration
async def test_an_unknown_approval_is_a_404(client: AsyncClient) -> None:
    assert (await client.get("/v1/approvals/01JZZZZZZZZZZZZZZZZZZZZZZZ")).status_code == 404


@pytest.mark.integration
@pytest.mark.req("FR-307")
async def test_the_task_audit_trail_is_readable_and_verifiable(
    client: AsyncClient, runtime_db: Settings, scheduler: Scheduler
) -> None:
    task = await install_plan(runtime_db, notify_plan(), ceiling=Capability.L3)
    await scheduler.tick()

    trail = await client.get(f"/v1/tasks/{task.id}/audit")
    assert trail.status_code == 200, trail.text
    events = [r["event_type"] for r in trail.json()["items"]]
    assert AuditEvent.APPROVAL_REQUESTED in events
    assert AuditEvent.POLICY_CONFIRM in events

    filtered = await client.get(
        "/v1/audit", params={"task_id": task.id, "event_type": AuditEvent.APPROVAL_REQUESTED}
    )
    assert filtered.status_code == 200
    assert len(filtered.json()["items"]) == 1

    verified = await client.post("/v1/audit/verify")
    assert verified.status_code == 200, verified.text
    assert verified.json()["ok"] is True, verified.json()
