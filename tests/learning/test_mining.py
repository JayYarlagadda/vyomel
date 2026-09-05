"""Frequent-sequence mining over normalized action signatures (FR-901)."""

from __future__ import annotations

import pytest

from vyomel.learning.mining import mine_frequent_sequences, sequences_from_actions
from vyomel.learning.signatures import ObservedAction, normalize_action


def _pipeline(task_id: str, path: str) -> list[ObservedAction]:
    return [
        ObservedAction(tool="fs.list_dir", parameters={"path": path}, task_id=task_id),
        ObservedAction(
            tool="fs.read_file",
            parameters={"path": f"{path}/notes.md"},
            task_id=task_id,
        ),
        ObservedAction(
            tool="fs.write_file",
            parameters={"path": f"{path}/out.md", "content": "x"},
            task_id=task_id,
        ),
        ObservedAction(
            tool="task.report",
            parameters={"summary": "done"},
            task_id=task_id,
        ),
    ]


@pytest.mark.req("FR-901")
def test_normalize_strips_values() -> None:
    a = normalize_action("fs.read_file", {"path": "/a/notes.md"})
    b = normalize_action("fs.read_file", {"path": "/b/other.md"})
    assert a.key() == b.key()
    assert a.target_type == "path"
    assert "path:str" in a.param_shape


@pytest.mark.req("FR-901")
def test_mining_finds_recurring_pipeline() -> None:
    actions: list[ObservedAction] = []
    for i in range(3):
        actions.extend(_pipeline(f"task{i}", f"/workspace/proj{i}"))
    # Noise task with a different shape should not inflate support alone.
    actions.append(
        ObservedAction(tool="shell.run", parameters={"argv": ["git", "status"]}, task_id="noise")
    )
    sequences = sequences_from_actions(actions)
    found = mine_frequent_sequences(sequences, min_support=3, min_length=3)
    assert found
    top = found[0]
    assert top.support >= 3
    assert top.length >= 3
    tools = [sig.tool for sig in top.signatures]
    assert tools[:3] == ["fs.list_dir", "fs.read_file", "fs.write_file"]


@pytest.mark.req("FR-901")
def test_support_below_threshold_yields_nothing() -> None:
    actions = _pipeline("t1", "/a") + _pipeline("t2", "/b")
    sequences = sequences_from_actions(actions)
    found = mine_frequent_sequences(sequences, min_support=3, min_length=3)
    assert found == []
