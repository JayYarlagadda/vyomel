"""Postgres-backed workflow store (migration 0009, FR-901-903)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from vyomel.core.types import Capability
from vyomel.learning.proposal import (
    WorkflowParameter,
    WorkflowProposal,
    WorkflowStepTemplate,
)
from vyomel.learning.store import WorkflowError, WorkflowNotFoundError
from vyomel.store.models import Workflow, WorkflowSuppression


def _row_to_proposal(row: Workflow) -> WorkflowProposal:
    return WorkflowProposal(
        id=row.id,
        name=row.name,
        description=row.description,
        source=row.source,
        definition=[WorkflowStepTemplate.model_validate(s) for s in (row.definition or [])],
        parameters=[WorkflowParameter.model_validate(p) for p in (row.parameters or [])],
        occurrence_count=row.occurrence_count,
        trust_level=row.trust_level,
        status=row.status,
        pattern_key=row.pattern_key,
        supporting_task_ids=[],
    )


class PostgresWorkflowStore:
    """Durable WorkflowStore backed by ``workflows`` + ``workflow_suppressions``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def put(self, proposal: WorkflowProposal) -> WorkflowProposal:
        if await self.is_suppressed(proposal.pattern_key):
            raise WorkflowError(
                "pattern is on the suppression list",
                detail={"pattern_key": proposal.pattern_key},
            )
        existing = await self._session.execute(
            select(Workflow).where(
                Workflow.pattern_key == proposal.pattern_key,
                Workflow.status != "rejected",
            )
        )
        row = existing.scalars().first()
        definition = [s.model_dump(mode="json") for s in proposal.definition]
        parameters = [p.model_dump(mode="json") for p in proposal.parameters]
        now = datetime.now(UTC)
        if row is not None:
            row.occurrence_count = max(row.occurrence_count, proposal.occurrence_count)
            row.definition = definition
            row.parameters = parameters
            row.trust_level = proposal.trust_level
            row.name = proposal.name
            row.description = proposal.description
            row.updated_at = now
            await self._session.flush()
            return _row_to_proposal(row)

        row = Workflow(
            id=proposal.id,
            name=proposal.name,
            description=proposal.description,
            source=proposal.source,
            definition=definition,
            parameters=parameters,
            occurrence_count=proposal.occurrence_count,
            pattern_key=proposal.pattern_key,
            status=proposal.status,
            trust_level=proposal.trust_level,
            accepted_at=now if proposal.status == "accepted" else None,
        )
        self._session.add(row)
        await self._session.flush()
        return _row_to_proposal(row)

    async def get(self, workflow_id: str) -> WorkflowProposal | None:
        row = await self._session.get(Workflow, workflow_id)
        return _row_to_proposal(row) if row is not None else None

    async def list(self, *, status: str | None = None) -> list[WorkflowProposal]:
        stmt = select(Workflow)
        if status is not None:
            stmt = stmt.where(Workflow.status == status)
        result = await self._session.execute(stmt)
        items = [_row_to_proposal(r) for r in result.scalars().all()]
        return sorted(items, key=lambda w: (-w.occurrence_count, w.name))

    async def update(self, proposal: WorkflowProposal) -> WorkflowProposal:
        row = await self._session.get(Workflow, proposal.id)
        if row is None:
            raise WorkflowNotFoundError(f"unknown workflow: {proposal.id}")
        row.name = proposal.name
        row.description = proposal.description
        row.source = proposal.source
        row.definition = [s.model_dump(mode="json") for s in proposal.definition]
        row.parameters = [p.model_dump(mode="json") for p in proposal.parameters]
        row.occurrence_count = proposal.occurrence_count
        row.pattern_key = proposal.pattern_key
        row.status = proposal.status
        row.trust_level = proposal.trust_level
        if proposal.status == "accepted" and row.accepted_at is None:
            row.accepted_at = datetime.now(UTC)
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        return _row_to_proposal(row)

    async def is_suppressed(self, pattern_key: str) -> bool:
        row = await self._session.get(WorkflowSuppression, pattern_key)
        return row is not None

    async def suppress(self, pattern_key: str) -> None:
        existing = await self._session.get(WorkflowSuppression, pattern_key)
        if existing is None:
            self._session.add(WorkflowSuppression(pattern_key=pattern_key))
            await self._session.flush()

    async def clear(self) -> None:
        """Test helper: wipe workflows + suppressions."""
        await self._session.execute(delete(Workflow))
        await self._session.execute(delete(WorkflowSuppression))
        await self._session.flush()


def proposal_from_row(row: Workflow) -> WorkflowProposal:
    return _row_to_proposal(row)


async def accept_workflow_pg(
    store: PostgresWorkflowStore, workflow_id: str
) -> WorkflowProposal:
    proposal = await store.get(workflow_id)
    if proposal is None:
        raise WorkflowNotFoundError(f"unknown workflow: {workflow_id}")
    if proposal.status == "rejected":
        raise WorkflowError("rejected workflows cannot be accepted")
    if proposal.trust_level > Capability.L2:
        raise WorkflowError("learned workflow trust_level cannot exceed L2")
    updated = proposal.model_copy(update={"status": "accepted"})
    return await store.update(updated)


async def reject_workflow_pg(
    store: PostgresWorkflowStore, workflow_id: str
) -> WorkflowProposal:
    proposal = await store.get(workflow_id)
    if proposal is None:
        raise WorkflowNotFoundError(f"unknown workflow: {workflow_id}")
    updated = proposal.model_copy(update={"status": "rejected"})
    await store.update(updated)
    await store.suppress(proposal.pattern_key)
    return updated
