"""Postgres workflow store + auto-mine (M15 harden)."""

from __future__ import annotations

import pytest

from vyomel.core.config import Settings
from vyomel.core.types import Capability
from vyomel.learning.pg_store import (
    PostgresWorkflowStore,
    accept_workflow_pg,
    reject_workflow_pg,
)
from vyomel.learning.service import auto_mine_after_task, mine_and_propose_pg
from vyomel.learning.signatures import ObservedAction
from vyomel.store.db import session_scope


def _corpus(n: int = 3) -> list[ObservedAction]:
    out: list[ObservedAction] = []
    for i in range(n):
        root = f"/workspace/p{i}"
        tid = f"task{i}"
        out.extend(
            [
                ObservedAction(tool="fs.list_dir", parameters={"path": root}, task_id=tid),
                ObservedAction(
                    tool="fs.read_file",
                    parameters={"path": f"{root}/in.md"},
                    task_id=tid,
                ),
                ObservedAction(
                    tool="fs.write_file",
                    parameters={"path": f"{root}/out.md", "content": "ok"},
                    task_id=tid,
                ),
            ]
        )
    return out


@pytest.mark.integration
@pytest.mark.req("FR-901")
async def test_postgres_mine_persist_accept(runtime_db: Settings) -> None:
    async with session_scope() as session:
        store = PostgresWorkflowStore(session)
        await store.clear()
        proposals = await mine_and_propose_pg(session, _corpus())
        assert proposals
        proposal = proposals[0]
        loaded = await store.get(proposal.id)
        assert loaded is not None
        assert loaded.status == "proposed"
        accepted = await accept_workflow_pg(store, proposal.id)
        assert accepted.status == "accepted"
        again = await store.get(proposal.id)
        assert again is not None
        assert again.status == "accepted"


@pytest.mark.integration
@pytest.mark.req("FR-903")
async def test_postgres_reject_suppresses(runtime_db: Settings) -> None:
    async with session_scope() as session:
        store = PostgresWorkflowStore(session)
        await store.clear()
        proposals = await mine_and_propose_pg(session, _corpus())
        proposal = proposals[0]
        await reject_workflow_pg(store, proposal.id)
        assert await store.is_suppressed(proposal.pattern_key)
        second = await mine_and_propose_pg(session, _corpus())
        assert all(p.pattern_key != proposal.pattern_key for p in second)


@pytest.mark.integration
@pytest.mark.req("FR-901")
async def test_auto_mine_disabled_by_default_in_runtime(runtime_db: Settings) -> None:
    # runtime_db fixture sets workflow_auto_mine=False
    async with session_scope() as session:
        out = await auto_mine_after_task(
            session,
            settings=runtime_db,
            tool_capabilities={"fs.list_dir": Capability.L0},
        )
        assert out == []
