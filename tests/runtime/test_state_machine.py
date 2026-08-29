"""Action state machine (FR-203).

Two properties the rest of the runtime is allowed to assume:

1. The Python table equals the markdown table in docs/07-EXECUTION-ENGINE.md §3.
2. The only trigger that lands on SUCCEEDED is verification_pass — there is no
   back door that lets a tool return value become a success.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from astra.core.errors import IllegalTransitionError
from astra.core.types import ActionStatus, TaskStatus
from astra.runtime.state import (
    ACTION_TRANSITIONS,
    ActionTrigger,
    TaskTrigger,
    apply_action,
    apply_task,
)

ROOT = Path(__file__).resolve().parents[2]
ENGINE_DOC = ROOT / "docs" / "07-EXECUTION-ENGINE.md"

# Phrases copied from the markdown table. If the doc wording changes, this
# mapping fails closed (unmapped phrase) rather than silently dropping a row.
_TRIGGER_PHRASE: dict[str, ActionTrigger] = {
    "dependencies satisfied": ActionTrigger.DEPENDENCIES_SATISFIED,
    "task cancelled": ActionTrigger.TASK_CANCELLED,
    "policy returned `CONFIRM`": ActionTrigger.POLICY_CONFIRM,
    "policy returned `DENY`": ActionTrigger.POLICY_DENY,
    "enqueued to Redis": ActionTrigger.ENQUEUED,
    "approval `APPROVED` / `MODIFIED`": ActionTrigger.APPROVAL_GRANTED,
    "`REJECTED` or `EXPIRED`": ActionTrigger.APPROVAL_REJECTED,
    "worker claimed": ActionTrigger.WORKER_CLAIMED,
    "lease reaper": ActionTrigger.LEASE_EXPIRED,
    "tool ok **and** verification `PASS`": ActionTrigger.VERIFICATION_PASS,
    "tool ok, verification `NO_METHOD`": ActionTrigger.VERIFICATION_NO_METHOD,
    "tool error non-retryable, or verification `FAIL`, or retries exhausted": (
        ActionTrigger.TOOL_FAILED_TERMINAL
    ),
    "retryable error, retries remain": ActionTrigger.TOOL_FAILED_RETRYABLE,
    "lease expired (worker died)": ActionTrigger.LEASE_EXPIRED,
    "compensation on cancel": ActionTrigger.COMPENSATED,
}

_STATUS = re.compile(r"`([A-Z_]+)`")


def _parse_doc_transitions() -> set[tuple[ActionStatus, ActionStatus, ActionTrigger]]:
    text = ENGINE_DOC.read_text(encoding="utf-8")
    start = text.index("## 3. Action state machine")
    end = text.index("## 4. Dispatch and durability")
    parsed: set[tuple[ActionStatus, ActionStatus, ActionTrigger]] = set()

    for line in text[start:end].splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or cells[0] in {"From", ":---"} or set(cells[0]) <= {"-", " "}:
            continue
        if cells[0].startswith("---") or "---" in cells[0]:
            continue

        source_cell, dest_cell, trigger_cell = cells[0], cells[1], cells[2]
        dest_match = _STATUS.search(dest_cell)
        if dest_match is None:
            continue
        dest = ActionStatus(dest_match.group(1))
        trigger = _TRIGGER_PHRASE[trigger_cell]

        if source_cell.startswith("any "):
            sources = [s for s in ActionStatus if not s.is_terminal]
        else:
            src_match = _STATUS.search(source_cell)
            assert src_match is not None, line
            sources = [ActionStatus(src_match.group(1))]

        for source in sources:
            parsed.add((source, dest, trigger))

    return parsed


@pytest.mark.req("FR-203")
def test_python_table_matches_the_spec_document() -> None:
    from_doc = _parse_doc_transitions()
    assert from_doc, "parser found no rows — the document section headings moved"
    missing = from_doc - ACTION_TRANSITIONS
    extra = ACTION_TRANSITIONS - from_doc
    assert missing == set(), f"code is missing spec rows: {missing}"
    assert extra == set(), f"code has rows not in the spec: {extra}"


@pytest.mark.req("FR-203")
def test_only_verification_pass_reaches_succeeded() -> None:
    into_succeeded = {
        (src, trig) for src, dest, trig in ACTION_TRANSITIONS if dest is ActionStatus.SUCCEEDED
    }
    assert into_succeeded == {(ActionStatus.RUNNING, ActionTrigger.VERIFICATION_PASS)}


@pytest.mark.req("FR-203")
def test_unverified_is_not_a_back_door_to_succeeded() -> None:
    assert apply_action(ActionStatus.RUNNING, ActionTrigger.VERIFICATION_NO_METHOD) is (
        ActionStatus.UNVERIFIED
    )
    with pytest.raises(IllegalTransitionError):
        apply_action(ActionStatus.UNVERIFIED, ActionTrigger.VERIFICATION_PASS)


@pytest.mark.req("FR-203")
def test_illegal_transitions_raise_rather_than_mutate() -> None:
    with pytest.raises(IllegalTransitionError) as exc:
        apply_action(ActionStatus.PLANNED, ActionTrigger.VERIFICATION_PASS)
    assert exc.value.code.value == "ILLEGAL_TRANSITION"
    assert exc.value.detail["from"] == "PLANNED"


@pytest.mark.req("FR-203")
def test_cancel_from_every_non_terminal() -> None:
    for status in ActionStatus:
        if status.is_terminal:
            with pytest.raises(IllegalTransitionError):
                apply_action(status, ActionTrigger.TASK_CANCELLED)
        else:
            assert apply_action(status, ActionTrigger.TASK_CANCELLED) is ActionStatus.CANCELLED


@pytest.mark.req("FR-203")
def test_succeeded_compensates_to_rolled_back() -> None:
    assert apply_action(ActionStatus.SUCCEEDED, ActionTrigger.COMPENSATED) is (
        ActionStatus.ROLLED_BACK
    )


@pytest.mark.req("FR-203")
@given(
    source=st.sampled_from(list(ActionStatus)),
    trigger=st.sampled_from(list(ActionTrigger)),
)
def test_apply_agrees_with_the_table(source: ActionStatus, trigger: ActionTrigger) -> None:
    matches = [dest for src, dest, trig in ACTION_TRANSITIONS if src is source and trig is trigger]
    if not matches:
        with pytest.raises(IllegalTransitionError):
            apply_action(source, trigger)
        return
    assert apply_action(source, trigger) is matches[0]


@pytest.mark.req("FR-203")
def test_task_machine_rejects_terminal_to_running() -> None:
    with pytest.raises(IllegalTransitionError):
        apply_task(TaskStatus.SUCCEEDED, TaskTrigger.FIRST_DISPATCH)


@pytest.mark.req("FR-203")
def test_needs_human_is_resumable_by_user_input_only() -> None:
    assert not TaskStatus.NEEDS_HUMAN.is_terminal
    assert apply_task(TaskStatus.NEEDS_HUMAN, TaskTrigger.HUMAN_REPLIED) is TaskStatus.RUNNING
    with pytest.raises(IllegalTransitionError):
        apply_task(TaskStatus.NEEDS_HUMAN, TaskTrigger.ALL_SUCCEEDED)
