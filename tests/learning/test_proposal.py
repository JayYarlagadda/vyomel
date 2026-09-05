"""Parameterized workflow proposals (FR-902)."""

from __future__ import annotations

import pytest

from vyomel.core.types import Capability
from vyomel.learning.mining import mine_frequent_sequences, sequences_from_actions
from vyomel.learning.proposal import bind_parameters, propose_workflows
from vyomel.learning.service import mine_and_propose
from vyomel.learning.signatures import ObservedAction
from vyomel.learning.store import MemoryWorkflowStore, reset_workflow_store


def _corpus(n: int = 3) -> list[ObservedAction]:
    actions: list[ObservedAction] = []
    for i in range(n):
        root = f"/workspace/p{i}"
        tid = f"task{i}"
        actions.extend(
            [
                ObservedAction(tool="fs.list_dir", parameters={"path": root}, task_id=tid),
                ObservedAction(
                    tool="fs.read_file",
                    parameters={"path": f"{root}/in.md"},
                    task_id=tid,
                ),
                ObservedAction(
                    tool="fs.write_file",
                    parameters={"path": f"{root}/out.md", "content": f"body-{i}"},
                    task_id=tid,
                ),
            ]
        )
    return actions


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_workflow_store()


@pytest.mark.req("FR-902")
def test_proposal_parameterizes_varying_values() -> None:
    actions = _corpus(3)
    sequences = sequences_from_actions(actions)
    patterns = mine_frequent_sequences(sequences, min_support=3, min_length=3)
    by_task: dict[str, list[ObservedAction]] = {}
    for action in actions:
        assert action.task_id is not None
        by_task.setdefault(action.task_id, []).append(action)
    corpus = list(by_task.items())
    proposals = propose_workflows(
        patterns,
        corpus,
        min_support=3,
        tool_capabilities={
            "fs.list_dir": Capability.L0,
            "fs.read_file": Capability.L0,
            "fs.write_file": Capability.L1,
        },
    )
    assert proposals
    proposal = proposals[0]
    assert proposal.occurrence_count >= 3
    assert proposal.status == "proposed"
    assert proposal.trust_level <= Capability.L2
    assert proposal.parameters  # path/content varied
    names = {p.name for p in proposal.parameters}
    assert any("path" in n for n in names)


@pytest.mark.req("FR-902")
def test_bind_parameters_expands_template() -> None:
    store = MemoryWorkflowStore()
    proposals = mine_and_propose(_corpus(3), store=store)
    proposal = proposals[0]
    values = {p.name: f"value-for-{p.name}" for p in proposal.parameters}
    steps = bind_parameters(proposal, values)
    assert len(steps) == len(proposal.definition)
    flat = str(steps)
    assert "$param" not in flat
    for p in proposal.parameters:
        assert f"value-for-{p.name}" in flat


@pytest.mark.req("FR-902")
def test_trust_level_capped_at_l2() -> None:
    actions = _corpus(3)
    # Inject an L3 tool into the recurring pattern.
    for i in range(3):
        actions.append(
            ObservedAction(
                tool="email.send",
                parameters={"to": [f"a{i}@t.test"], "subject": "x", "body": "y"},
                task_id=f"task{i}",
            )
        )
    # Rebuild so each task has list→read→write→send
    rebuilt: list[ObservedAction] = []
    for i in range(3):
        root = f"/workspace/p{i}"
        tid = f"task{i}"
        rebuilt.extend(
            [
                ObservedAction(tool="fs.list_dir", parameters={"path": root}, task_id=tid),
                ObservedAction(
                    tool="fs.read_file", parameters={"path": f"{root}/in.md"}, task_id=tid
                ),
                ObservedAction(
                    tool="email.send",
                    parameters={"to": [f"a{i}@t.test"], "subject": "x", "body": "y"},
                    task_id=tid,
                ),
            ]
        )
    proposals = mine_and_propose(
        rebuilt,
        tool_capabilities={
            "fs.list_dir": Capability.L0,
            "fs.read_file": Capability.L0,
            "email.send": Capability.L3,
        },
    )
    assert proposals
    assert all(p.trust_level <= Capability.L2 for p in proposals)
