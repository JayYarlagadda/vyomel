"""Core enumerations shared across every layer.

These mirror the Postgres enums declared in docs/03-DATA-MODEL.md section 6.
Values are append-only: reordering or removing one is a breaking schema change.
"""

from __future__ import annotations

from enum import StrEnum


class Capability(StrEnum):
    """Capability lattice from docs/06-SECURITY-PERMISSIONS.md section 2."""

    L0 = "L0"  # observe only
    L1 = "L1"  # reversible local change
    L2 = "L2"  # persistent local change
    L3 = "L3"  # external communication
    L4 = "L4"  # financial / destructive / security-sensitive

    @property
    def rank(self) -> int:
        return _CAPABILITY_RANK[self]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Capability):
            return NotImplemented
        return self.rank < other.rank

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Capability):
            return NotImplemented
        return self.rank <= other.rank

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Capability):
            return NotImplemented
        return self.rank > other.rank

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Capability):
            return NotImplemented
        return self.rank >= other.rank

    def raised_by(self, levels: int) -> Capability:
        """Escalate by ``levels``, saturating at L4.

        Escalation rules may only ever raise a level, never lower it, so this
        deliberately has no counterpart that decreases capability.
        """
        target = min(self.rank + max(levels, 0), Capability.L4.rank)
        return _RANK_TO_CAPABILITY[target]


_CAPABILITY_RANK: dict[Capability, int] = {
    Capability.L0: 0,
    Capability.L1: 1,
    Capability.L2: 2,
    Capability.L3: 3,
    Capability.L4: 4,
}
_RANK_TO_CAPABILITY: dict[int, Capability] = {v: k for k, v in _CAPABILITY_RANK.items()}


class TaskStatus(StrEnum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    PAUSED = "PAUSED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    NEEDS_HUMAN = "NEEDS_HUMAN"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_TASK_STATUSES


_TERMINAL_TASK_STATUSES = frozenset({TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED})


class StepStatus(StrEnum):
    PLANNED = "PLANNED"
    READY = "READY"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class ActionStatus(StrEnum):
    PLANNED = "PLANNED"
    READY = "READY"
    DISPATCHED = "DISPATCHED"
    RUNNING = "RUNNING"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    SUCCEEDED = "SUCCEEDED"
    UNVERIFIED = "UNVERIFIED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_ACTION_STATUSES


_TERMINAL_ACTION_STATUSES = frozenset(
    {
        ActionStatus.SUCCEEDED,
        ActionStatus.UNVERIFIED,
        ActionStatus.FAILED,
        ActionStatus.ROLLED_BACK,
        ActionStatus.CANCELLED,
    }
)


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    MODIFIED = "MODIFIED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class VerifyOutcome(StrEnum):
    PASS = "PASS"  # noqa: S105 -- verification outcome, not a credential
    FAIL = "FAIL"
    NO_METHOD = "NO_METHOD"


class Decision(StrEnum):
    """Policy engine verdict."""

    ALLOW = "ALLOW"
    CONFIRM = "CONFIRM"
    DENY = "DENY"


class Sensitivity(StrEnum):
    """Data classification driving privacy-based model routing (FR-703)."""

    PUBLIC = "PUBLIC"
    PERSONAL = "PERSONAL"
    SENSITIVE = "SENSITIVE"


class Trust(StrEnum):
    """Provenance of a context block, used for prompt-injection defense."""

    USER = "user"
    SYSTEM = "system"
    MEMORY = "memory"
    TOOL_TRUSTED = "tool_trusted"
    TOOL_UNTRUSTED = "tool_untrusted"


class TaskOrigin(StrEnum):
    CLI = "cli"
    API = "api"
    VOICE = "voice"
    SCHEDULE = "schedule"
    WORKFLOW = "workflow"


class EntityType(StrEnum):
    PERSON = "person"
    PROJECT = "project"
    DOCUMENT = "document"
    APPLICATION = "application"
    TASK_REF = "task_ref"
    PREFERENCE = "preference"
    WORKFLOW = "workflow"
    ORGANIZATION = "organization"
    EVENT = "event"
    PLACE = "place"


class EntityRelationType(StrEnum):
    BELONGS_TO = "belongs_to"
    AUTHORED_BY = "authored_by"
    MENTIONS = "mentions"
    DEPENDS_ON = "depends_on"
    SCHEDULED_FOR = "scheduled_for"
    LOCATED_AT = "located_at"
    RELATED_TO = "related_to"
    DERIVED_FROM = "derived_from"


class ActuationTier(StrEnum):
    """Control hierarchy from docs/02-ARCHITECTURE.md section 6.

    Lower tiers are more reliable and more precisely verifiable. Tier 4 always
    escalates capability because coordinate clicking has unbounded blast radius.
    """

    NATIVE_API = "1"
    ACCESSIBILITY = "2"
    DOM = "3"
    VISION = "4"
