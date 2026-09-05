"""Workflow persistence and acceptance (FR-903, FR-310)."""

from __future__ import annotations

from threading import Lock
from typing import Protocol

from vyomel.core.errors import ErrorCode, VyomelError
from vyomel.core.types import Capability
from vyomel.learning.proposal import WorkflowProposal, bind_parameters


class WorkflowError(VyomelError):
    code = ErrorCode.INVALID_PARAMETERS


class WorkflowNotFoundError(VyomelError):
    code = ErrorCode.NOT_FOUND


class WorkflowStore(Protocol):
    def put(self, proposal: WorkflowProposal) -> WorkflowProposal: ...

    def get(self, workflow_id: str) -> WorkflowProposal | None: ...

    def list(self, *, status: str | None = None) -> list[WorkflowProposal]: ...

    def update(self, proposal: WorkflowProposal) -> WorkflowProposal: ...

    def is_suppressed(self, pattern_key: str) -> bool: ...

    def suppress(self, pattern_key: str) -> None: ...


class MemoryWorkflowStore:
    """Process-local store for tests and offline mining."""

    def __init__(self) -> None:
        self._items: dict[str, WorkflowProposal] = {}
        self._suppressed: set[str] = set()
        self._lock = Lock()

    def put(self, proposal: WorkflowProposal) -> WorkflowProposal:
        with self._lock:
            if proposal.pattern_key in self._suppressed:
                raise WorkflowError(
                    "pattern is on the suppression list",
                    detail={"pattern_key": proposal.pattern_key},
                )
            # Dedupe by pattern: refresh occurrence count on an existing proposal.
            for existing in self._items.values():
                if existing.pattern_key == proposal.pattern_key and existing.status != "rejected":
                    updated = existing.model_copy(
                        update={
                            "occurrence_count": max(
                                existing.occurrence_count, proposal.occurrence_count
                            ),
                            "supporting_task_ids": sorted(
                                set(existing.supporting_task_ids)
                                | set(proposal.supporting_task_ids)
                            ),
                            "definition": proposal.definition,
                            "parameters": proposal.parameters,
                            "trust_level": proposal.trust_level,
                        }
                    )
                    self._items[existing.id] = updated
                    return updated
            self._items[proposal.id] = proposal
            return proposal

    def get(self, workflow_id: str) -> WorkflowProposal | None:
        with self._lock:
            return self._items.get(workflow_id)

    def list(self, *, status: str | None = None) -> list[WorkflowProposal]:
        with self._lock:
            items = list(self._items.values())
        if status is not None:
            items = [w for w in items if w.status == status]
        return sorted(items, key=lambda w: (-w.occurrence_count, w.name))

    def update(self, proposal: WorkflowProposal) -> WorkflowProposal:
        with self._lock:
            if proposal.id not in self._items:
                raise WorkflowNotFoundError(f"unknown workflow: {proposal.id}")
            self._items[proposal.id] = proposal
            return proposal

    def is_suppressed(self, pattern_key: str) -> bool:
        with self._lock:
            return pattern_key in self._suppressed

    def suppress(self, pattern_key: str) -> None:
        with self._lock:
            self._suppressed.add(pattern_key)


_STORE: MemoryWorkflowStore | None = None
_STORE_LOCK = Lock()


def get_workflow_store() -> MemoryWorkflowStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = MemoryWorkflowStore()
        return _STORE


def reset_workflow_store() -> None:
    global _STORE
    with _STORE_LOCK:
        _STORE = MemoryWorkflowStore()


def accept_workflow(store: WorkflowStore, workflow_id: str) -> WorkflowProposal:
    proposal = store.get(workflow_id)
    if proposal is None:
        raise WorkflowNotFoundError(f"unknown workflow: {workflow_id}")
    if proposal.status == "rejected":
        raise WorkflowError("rejected workflows cannot be accepted")
    if proposal.trust_level > Capability.L2:
        raise WorkflowError("learned workflow trust_level cannot exceed L2")
    updated = proposal.model_copy(update={"status": "accepted"})
    return store.update(updated)


def reject_workflow(store: WorkflowStore, workflow_id: str) -> WorkflowProposal:
    proposal = store.get(workflow_id)
    if proposal is None:
        raise WorkflowNotFoundError(f"unknown workflow: {workflow_id}")
    updated = proposal.model_copy(update={"status": "rejected"})
    store.update(updated)
    store.suppress(proposal.pattern_key)
    return updated


def require_accepted(store: WorkflowStore, workflow_id: str) -> WorkflowProposal:
    proposal = store.get(workflow_id)
    if proposal is None:
        raise WorkflowNotFoundError(f"unknown workflow: {workflow_id}")
    if proposal.status != "accepted":
        raise WorkflowError(
            "workflow is not accepted and cannot be invoked",
            detail={"status": proposal.status, "workflow_id": workflow_id},
            code=ErrorCode.PERMISSION_DENIED,
        )
    return proposal


def expand_workflow(
    store: WorkflowStore, workflow_id: str, values: dict[str, object]
) -> list[dict[str, object]]:
    proposal = require_accepted(store, workflow_id)
    try:
        return bind_parameters(proposal, values)
    except ValueError as exc:
        raise WorkflowError(str(exc)) from exc
