"""DAG readiness and acyclicity (FR-204)."""

from __future__ import annotations

import pytest

from vyomel.core.types import ActionStatus
from vyomel.runtime.dag import ActionNode, CyclicPlanError, ready_ids, reverse_topo, validate_acyclic


def _node(
    node_id: str,
    *deps: str,
    status: ActionStatus = ActionStatus.PLANNED,
    tolerates_unverified: bool = False,
) -> ActionNode:
    return ActionNode(
        id=node_id,
        status=status,
        depends_on=deps,
        step_id=f"step-{node_id}",
        tolerates_unverified=tolerates_unverified,
    )


@pytest.mark.req("FR-204")
def test_roots_are_ready_and_dependents_wait() -> None:
    nodes = [
        _node("a"),
        _node("b"),
        _node("c", "a", "b"),
        _node("d", "c"),
    ]
    assert ready_ids(nodes) == ["a", "b"]

    done = [
        _node("a", status=ActionStatus.SUCCEEDED),
        _node("b", status=ActionStatus.SUCCEEDED),
        _node("c", "a", "b"),
        _node("d", "c"),
    ]
    assert ready_ids(done) == ["c"]


@pytest.mark.req("FR-204")
def test_failed_dependency_blocks_not_fails_the_child() -> None:
    # Readiness is a snapshot function. Promoting a blocked child to FAILED is
    # the dispatcher's job; this function must not invent that policy.
    nodes = [
        _node("a", status=ActionStatus.FAILED),
        _node("b", "a"),
    ]
    assert ready_ids(nodes) == []


@pytest.mark.req("FR-204")
def test_unverified_is_accepted_only_when_the_dependent_opts_in() -> None:
    upstream = _node("a", status=ActionStatus.UNVERIFIED)
    strict = _node("b", "a")
    tolerant = _node("c", "a", tolerates_unverified=True)
    assert ready_ids([upstream, strict, tolerant]) == ["c"]


@pytest.mark.req("FR-204")
def test_cycle_is_rejected() -> None:
    with pytest.raises(CyclicPlanError) as exc:
        validate_acyclic([_node("a", "b"), _node("b", "a")])
    assert "cycle" in exc.value.user_message.lower()


@pytest.mark.req("FR-204")
def test_self_edge_and_dangling_dep_are_rejected() -> None:
    with pytest.raises(CyclicPlanError):
        validate_acyclic([_node("a", "a")])
    with pytest.raises(CyclicPlanError):
        validate_acyclic([_node("a", "missing")])


@pytest.mark.req("FR-204")
def test_reverse_topo_puts_sinks_first() -> None:
    # Compensation must undo later effects before earlier ones (07 §8).
    nodes = [_node("a"), _node("b", "a"), _node("c", "b"), _node("d")]
    order = reverse_topo(nodes)
    assert order.index("c") < order.index("b") < order.index("a")
