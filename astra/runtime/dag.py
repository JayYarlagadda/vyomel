"""DAG readiness and acyclicity.

Readiness is defined in docs/07-EXECUTION-ENGINE.md section 5: an action is
ready when every dependency is ``SUCCEEDED``, or ``UNVERIFIED`` *and* the
dependent step declared ``tolerates_unverified``. Cycle detection is Kahn's
algorithm so a cyclic handwritten plan fails at install time, not as a hang.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from astra.core.errors import AstraError, ErrorCode
from astra.core.types import ActionStatus


class CyclicPlanError(AstraError):
    code = ErrorCode.INVALID_PARAMETERS


@dataclass(frozen=True, slots=True)
class ActionNode:
    id: str
    status: ActionStatus
    depends_on: tuple[str, ...]
    step_id: str
    # Looked up from the parent step; stored here so readiness is a pure function
    # of this snapshot rather than a join the caller might forget.
    tolerates_unverified: bool = False


_SUCCESS = frozenset({ActionStatus.SUCCEEDED})
_SUCCESS_OR_UNVERIFIED = frozenset({ActionStatus.SUCCEEDED, ActionStatus.UNVERIFIED})


def validate_acyclic(nodes: list[ActionNode]) -> None:
    """Raise ``CyclicPlanError`` if the dependency graph has a cycle or a dangling edge."""
    ids = {node.id for node in nodes}
    incoming: dict[str, int] = {node.id: 0 for node in nodes}
    outgoing: dict[str, list[str]] = defaultdict(list)

    for node in nodes:
        for dep in node.depends_on:
            if dep not in ids:
                raise CyclicPlanError(
                    f"Action {node.id} depends on unknown action {dep}",
                    detail={"action_id": node.id, "missing": dep},
                )
            if dep == node.id:
                raise CyclicPlanError(
                    f"Action {node.id} depends on itself",
                    detail={"action_id": node.id},
                )
            incoming[node.id] += 1
            outgoing[dep].append(node.id)

    ready: deque[str] = deque(node_id for node_id, count in incoming.items() if count == 0)
    seen = 0
    while ready:
        current = ready.popleft()
        seen += 1
        for nxt in outgoing[current]:
            incoming[nxt] -= 1
            if incoming[nxt] == 0:
                ready.append(nxt)

    if seen != len(nodes):
        cyclic = sorted(node_id for node_id, count in incoming.items() if count > 0)
        raise CyclicPlanError(
            "Plan contains a cycle",
            detail={"cyclic_actions": cyclic},
        )


def ready_ids(nodes: list[ActionNode]) -> list[str]:
    """PLANNED actions whose dependencies are all acceptably terminal.

    Order is the input order among currently-ready actions so tests (and the
    dispatcher) are deterministic without inventing a priority scheme.
    """
    by_id = {node.id: node for node in nodes}
    ready: list[str] = []
    for node in nodes:
        if node.status is not ActionStatus.PLANNED:
            continue
        acceptable = _SUCCESS_OR_UNVERIFIED if node.tolerates_unverified else _SUCCESS
        if all(by_id[dep].status in acceptable for dep in node.depends_on):
            ready.append(node.id)
    return ready


def reverse_topo(nodes: list[ActionNode]) -> list[str]:
    """Ids in reverse topological order — compensation on cancel (07 §8)."""
    validate_acyclic(nodes)
    incoming: dict[str, int] = {node.id: 0 for node in nodes}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        for dep in node.depends_on:
            incoming[node.id] += 1
            outgoing[dep].append(node.id)

    ready: deque[str] = deque(node_id for node_id, count in incoming.items() if count == 0)
    order: list[str] = []
    while ready:
        current = ready.popleft()
        order.append(current)
        for nxt in outgoing[current]:
            incoming[nxt] -= 1
            if incoming[nxt] == 0:
                ready.append(nxt)
    order.reverse()
    return order
