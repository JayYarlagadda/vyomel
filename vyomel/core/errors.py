"""Error hierarchy.

Two rules the rest of the system depends on:

1. ``retryable`` is a declared property of the error, never inferred by the
   runtime from a message string. The retry ladder in
   docs/07-EXECUTION-ENGINE.md section 6 keys off it directly.
2. ``user_message`` is safe to surface; ``detail`` may contain internals and is
   passed through the redaction filter before it reaches any sink.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    # retryable
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    TRANSIENT_IO = "TRANSIENT_IO"
    ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"

    # not retryable -- escalate to replan or human
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    INVALID_PARAMETERS = "INVALID_PARAMETERS"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    UNSUPPORTED = "UNSUPPORTED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    INTERNAL = "INTERNAL"

    # invariant violations -- these indicate a bug, never a user error
    ILLEGAL_TRANSITION = "ILLEGAL_TRANSITION"
    POLICY_INVARIANT = "POLICY_INVARIANT"
    PRIVACY_ROUTING = "PRIVACY_ROUTING"


_RETRYABLE: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.TIMEOUT,
        ErrorCode.RATE_LIMITED,
        ErrorCode.TRANSIENT_IO,
        ErrorCode.ELEMENT_NOT_FOUND,
    }
)

_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.PERMISSION_DENIED: 403,
    ErrorCode.INVALID_PARAMETERS: 422,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.TIMEOUT: 504,
    ErrorCode.DEADLINE_EXCEEDED: 504,
    ErrorCode.UNSUPPORTED: 501,
    ErrorCode.BUDGET_EXCEEDED: 402,
}


class VyomelError(Exception):
    """Base for every error Vyomel raises deliberately."""

    code: ErrorCode = ErrorCode.INTERNAL

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode | None = None,
        detail: dict[str, Any] | None = None,
        retryable: bool | None = None,
        observation: str | None = None,
    ) -> None:
        super().__init__(message)
        self.user_message = message
        self.code = code or type(self).code
        self.detail = detail or {}
        self.retryable = _RETRYABLE.__contains__(self.code) if retryable is None else retryable
        # What the environment looked like when this failed. Feeds the
        # "observe and adapt" rung of the failure ladder.
        self.observation = observation

    @property
    def http_status(self) -> int:
        return _HTTP_STATUS.get(self.code, 500)

    def to_problem(self, *, trace_id: str | None = None) -> dict[str, Any]:
        """RFC 9457 Problem Details representation."""
        problem: dict[str, Any] = {
            "type": f"https://vyomel.dev/errors/{self.code.value.lower().replace('_', '-')}",
            "title": self.code.value.replace("_", " ").title(),
            "status": self.http_status,
            "code": self.code.value,
            "detail": self.user_message,
            "retryable": self.retryable,
        }
        if trace_id:
            problem["trace_id"] = trace_id
        return problem


class ConfigError(VyomelError):
    code = ErrorCode.INVALID_PARAMETERS


class NotFoundError(VyomelError):
    code = ErrorCode.NOT_FOUND


class ConflictError(VyomelError):
    code = ErrorCode.CONFLICT


class ToolError(VyomelError):
    """Raised by tools. Never let a raw exception reach the planner (FR-608)."""


class PermissionDeniedError(VyomelError):
    code = ErrorCode.PERMISSION_DENIED


class BudgetExceededError(VyomelError):
    code = ErrorCode.BUDGET_EXCEEDED


class DeadlineExceededError(VyomelError):
    code = ErrorCode.DEADLINE_EXCEEDED


class VerificationFailedError(VyomelError):
    code = ErrorCode.VERIFICATION_FAILED


class IllegalTransitionError(VyomelError):
    """A state machine transition that the specification forbids.

    Always a bug. Logged at CRITICAL.
    """

    code = ErrorCode.ILLEGAL_TRANSITION


class PolicyInvariantViolation(VyomelError):
    """A security invariant was violated -- e.g. an attempt to auto-approve L4.

    Never recoverable, never suppressed.
    """

    code = ErrorCode.POLICY_INVARIANT


class PrivacyRoutingViolation(VyomelError):
    """Sensitive payload was about to reach a remote provider (FR-703)."""

    code = ErrorCode.PRIVACY_ROUTING
