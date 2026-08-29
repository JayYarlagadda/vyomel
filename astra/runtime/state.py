"""Action and task state machines.

The tables in this module *are* docs/07-EXECUTION-ENGINE.md sections 2 and 3.
Callers report what happened (a trigger); they do not pick a destination.
``tests/runtime/test_state_machine.py`` parses the markdown table and asserts
equality, so a one-sided edit is a failing test rather than a comment in a PR.
"""

from __future__ import annotations

from enum import StrEnum

from astra.core.errors import IllegalTransitionError
from astra.core.types import ActionStatus, TaskStatus


class ActionTrigger(StrEnum):
    DEPENDENCIES_SATISFIED = "dependencies_satisfied"
    TASK_CANCELLED = "task_cancelled"
    POLICY_CONFIRM = "policy_confirm"
    POLICY_DENY = "policy_deny"
    ENQUEUED = "enqueued"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_REJECTED = "approval_rejected"
    WORKER_CLAIMED = "worker_claimed"
    LEASE_EXPIRED = "lease_expired"
    VERIFICATION_PASS = "verification_pass"  # noqa: S105 — trigger name, not a credential
    VERIFICATION_NO_METHOD = "verification_no_method"
    TOOL_FAILED_TERMINAL = "tool_failed_terminal"
    TOOL_FAILED_RETRYABLE = "tool_failed_retryable"
    COMPENSATED = "compensated"


class TaskTrigger(StrEnum):
    PLAN_REQUESTED = "plan_requested"
    PLAN_INVALID = "plan_invalid"
    PLAN_VALIDATED = "plan_validated"
    FIRST_DISPATCH = "first_dispatch"
    APPROVAL_NEEDED = "approval_needed"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_REJECTED = "approval_rejected"
    PAUSED = "paused"
    RESUMED = "resumed"
    REPLAN_EXHAUSTED = "replan_exhausted"
    ALL_SUCCEEDED = "all_succeeded"
    REQUIRED_FAILED = "required_failed"
    CANCELLED = "cancelled"
    DEADLINE = "deadline"
    HUMAN_REPLIED = "human_replied"


# (source, dest, trigger) — dest is determined by (source, trigger) uniquely.
# "any non-terminal → CANCELLED" is expanded here so the table is exhaustive
# and a missing row is a missing capability, not an implicit branch.
ACTION_TRANSITIONS: frozenset[tuple[ActionStatus, ActionStatus, ActionTrigger]] = frozenset(
    {
        (ActionStatus.PLANNED, ActionStatus.READY, ActionTrigger.DEPENDENCIES_SATISFIED),
        (ActionStatus.READY, ActionStatus.WAITING_FOR_USER, ActionTrigger.POLICY_CONFIRM),
        (ActionStatus.READY, ActionStatus.FAILED, ActionTrigger.POLICY_DENY),
        (ActionStatus.READY, ActionStatus.DISPATCHED, ActionTrigger.ENQUEUED),
        (ActionStatus.WAITING_FOR_USER, ActionStatus.READY, ActionTrigger.APPROVAL_GRANTED),
        (ActionStatus.WAITING_FOR_USER, ActionStatus.FAILED, ActionTrigger.APPROVAL_REJECTED),
        (ActionStatus.DISPATCHED, ActionStatus.RUNNING, ActionTrigger.WORKER_CLAIMED),
        (ActionStatus.DISPATCHED, ActionStatus.READY, ActionTrigger.LEASE_EXPIRED),
        (ActionStatus.RUNNING, ActionStatus.SUCCEEDED, ActionTrigger.VERIFICATION_PASS),
        (ActionStatus.RUNNING, ActionStatus.UNVERIFIED, ActionTrigger.VERIFICATION_NO_METHOD),
        (ActionStatus.RUNNING, ActionStatus.FAILED, ActionTrigger.TOOL_FAILED_TERMINAL),
        (ActionStatus.RUNNING, ActionStatus.READY, ActionTrigger.TOOL_FAILED_RETRYABLE),
        (ActionStatus.RUNNING, ActionStatus.READY, ActionTrigger.LEASE_EXPIRED),
        (ActionStatus.SUCCEEDED, ActionStatus.ROLLED_BACK, ActionTrigger.COMPENSATED),
    }
    | {
        (status, ActionStatus.CANCELLED, ActionTrigger.TASK_CANCELLED)
        for status in ActionStatus
        if not status.is_terminal
    }
)

TASK_TRANSITIONS: frozenset[tuple[TaskStatus, TaskStatus, TaskTrigger]] = frozenset(
    {
        (TaskStatus.CREATED, TaskStatus.PLANNING, TaskTrigger.PLAN_REQUESTED),
        (TaskStatus.PLANNING, TaskStatus.FAILED, TaskTrigger.PLAN_INVALID),
        (TaskStatus.PLANNING, TaskStatus.READY, TaskTrigger.PLAN_VALIDATED),
        (TaskStatus.READY, TaskStatus.RUNNING, TaskTrigger.FIRST_DISPATCH),
        (TaskStatus.RUNNING, TaskStatus.WAITING_FOR_USER, TaskTrigger.APPROVAL_NEEDED),
        (TaskStatus.WAITING_FOR_USER, TaskStatus.RUNNING, TaskTrigger.APPROVAL_GRANTED),
        (TaskStatus.WAITING_FOR_USER, TaskStatus.FAILED, TaskTrigger.APPROVAL_REJECTED),
        (TaskStatus.RUNNING, TaskStatus.PAUSED, TaskTrigger.PAUSED),
        (TaskStatus.PAUSED, TaskStatus.RUNNING, TaskTrigger.RESUMED),
        (TaskStatus.RUNNING, TaskStatus.NEEDS_HUMAN, TaskTrigger.REPLAN_EXHAUSTED),
        (TaskStatus.NEEDS_HUMAN, TaskStatus.RUNNING, TaskTrigger.HUMAN_REPLIED),
        (TaskStatus.RUNNING, TaskStatus.SUCCEEDED, TaskTrigger.ALL_SUCCEEDED),
        (TaskStatus.RUNNING, TaskStatus.FAILED, TaskTrigger.REQUIRED_FAILED),
        (TaskStatus.RUNNING, TaskStatus.FAILED, TaskTrigger.DEADLINE),
        (TaskStatus.READY, TaskStatus.FAILED, TaskTrigger.DEADLINE),
    }
    | {
        (status, TaskStatus.CANCELLED, TaskTrigger.CANCELLED)
        for status in TaskStatus
        if not status.is_terminal
    }
)


def _index[S, T](rows: frozenset[tuple[S, S, T]]) -> dict[tuple[S, T], S]:
    indexed: dict[tuple[S, T], S] = {}
    for source, dest, trigger in rows:
        key = (source, trigger)
        existing = indexed.get(key)
        if existing is not None and existing is not dest:
            raise RuntimeError(f"Ambiguous transition {source} --{trigger}--> {existing}|{dest}")
        indexed[key] = dest
    return indexed


_ACTION_INDEX = _index(ACTION_TRANSITIONS)
_TASK_INDEX = _index(TASK_TRANSITIONS)


def apply_action(current: ActionStatus, trigger: ActionTrigger) -> ActionStatus:
    """Return the destination status, or raise if the spec forbids this trigger here."""
    dest = _ACTION_INDEX.get((current, trigger))
    if dest is None:
        raise IllegalTransitionError(
            f"Illegal action transition {current} --{trigger.value}--> ?",
            detail={
                "from": current.value,
                "trigger": trigger.value,
                "allowed_triggers": sorted(t.value for (src, t) in _ACTION_INDEX if src is current),
            },
        )
    return dest


def apply_task(current: TaskStatus, trigger: TaskTrigger) -> TaskStatus:
    dest = _TASK_INDEX.get((current, trigger))
    if dest is None:
        raise IllegalTransitionError(
            f"Illegal task transition {current} --{trigger.value}--> ?",
            detail={
                "from": current.value,
                "trigger": trigger.value,
                "allowed_triggers": sorted(t.value for (src, t) in _TASK_INDEX if src is current),
            },
        )
    return dest


def action_allowed(source: ActionStatus, dest: ActionStatus, trigger: ActionTrigger) -> bool:
    return (source, dest, trigger) in ACTION_TRANSITIONS
