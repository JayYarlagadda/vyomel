"""Capability lattice semantics."""

from __future__ import annotations

from itertools import pairwise

import pytest

from vyomel.core.types import ActionStatus, Capability, TaskStatus


@pytest.mark.req("FR-301")
def test_capability_ordering_is_total() -> None:
    order = [Capability.L0, Capability.L1, Capability.L2, Capability.L3, Capability.L4]
    for lower, higher in pairwise(order):
        assert lower < higher
        assert higher > lower
        assert lower <= lower


@pytest.mark.req("FR-301")
@pytest.mark.parametrize(
    ("start", "levels", "expected"),
    [
        (Capability.L0, 1, Capability.L1),
        (Capability.L1, 2, Capability.L3),
        (Capability.L3, 1, Capability.L4),
        (Capability.L4, 3, Capability.L4),  # saturates
        (Capability.L2, 0, Capability.L2),
    ],
)
def test_escalation_saturates_at_l4(start: Capability, levels: int, expected: Capability) -> None:
    assert start.raised_by(levels) is expected


@pytest.mark.req("FR-301")
def test_escalation_never_lowers() -> None:
    for capability in Capability:
        assert capability.raised_by(-5) >= capability


@pytest.mark.req("FR-203")
def test_terminal_status_sets() -> None:
    assert TaskStatus.SUCCEEDED.is_terminal
    assert not TaskStatus.RUNNING.is_terminal
    # NEEDS_HUMAN is quasi-terminal: resumable only by user input, so it must
    # not be classified as terminal or the scheduler would stop tracking it.
    assert not TaskStatus.NEEDS_HUMAN.is_terminal

    assert ActionStatus.UNVERIFIED.is_terminal
    assert not ActionStatus.DISPATCHED.is_terminal
