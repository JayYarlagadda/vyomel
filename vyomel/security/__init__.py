"""Security layer: classification, policy, approvals, and audit.

Nothing here decides *what* to do; it decides what is permitted, what needs a
human, and what gets recorded. The runtime consults this layer before every
dispatch, and the layering check forbids the dependency going the other way.
"""

from __future__ import annotations

from vyomel.security.capability import Classification, EscalationRules, Invocation, classify
from vyomel.security.policy import (
    DENY_ALL,
    Policy,
    PolicyDecision,
    PolicyRequest,
    PolicyStore,
    load_policy,
    safe_load_policy,
    variables_for,
)

__all__ = [
    "DENY_ALL",
    "Classification",
    "EscalationRules",
    "Invocation",
    "Policy",
    "PolicyDecision",
    "PolicyRequest",
    "PolicyStore",
    "classify",
    "load_policy",
    "safe_load_policy",
    "variables_for",
]
