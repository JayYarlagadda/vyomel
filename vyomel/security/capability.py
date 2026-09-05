"""Capability classification and escalation (FR-301).

docs/06-SECURITY-PERMISSIONS.md section 2. Classification is a property of the
**tool plus its resolved parameters**, never of the tool alone: ``fs.delete`` on
one scratch file and ``fs.delete`` on ``~/.ssh`` are not the same act.

Two properties this module guarantees, and which the tests pin:

1. Escalation only ever *raises* a level. There is no code path that lowers one,
   which is why :meth:`Capability.raised_by` has no inverse.
2. An invocation this module cannot classify is **L4**, not L0. Unclassified
   means "maximum caution" (rule 5, FR-302).

The tool layer cannot import this module (see the layering table), so the base
level arrives as data. The orchestrator is the seam that has both.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from vyomel.core.types import Capability, Trust
from vyomel.security.matching import any_match, domain_match

# Keys whose values are treated as filesystem paths for sensitivity matching.
# Matching every string against path globs would make a rule like "**/.env"
# fire on a note that merely mentions the filename.
PATH_KEYS: frozenset[str] = frozenset(
    {"path", "paths", "src", "source", "dst", "dest", "destination", "file", "files", "directory"}
)


@dataclass(frozen=True, slots=True)
class EscalationRules:
    """The ``escalation:`` block of ``config/policy.yaml``."""

    vision_tier_minimum: Capability = Capability.L2
    untrusted_taint_levels: int = 1
    untrusted_taint_minimum: Capability = Capability.L2
    bulk_threshold: int = 10
    bulk_levels: int = 1
    unknown_tool: Capability = Capability.L4
    sensitive_paths: tuple[str, ...] = ()
    sensitive_domains: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Invocation:
    """What is about to happen, in the terms classification needs."""

    tool: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    # None means the tool is unknown or declared no base level.
    base: Capability | None = None
    actuation_tier: int = 1
    trust: Trust = Trust.USER
    affected_count: int = 1


@dataclass(frozen=True, slots=True)
class Classification:
    level: Capability
    reasons: tuple[str, ...]

    @property
    def escalated(self) -> bool:
        return bool(self.reasons)


def classify(invocation: Invocation, rules: EscalationRules | None = None) -> Classification:
    """Final capability level, with the reason for every escalation applied."""
    rules = rules or EscalationRules()

    if invocation.base is None:
        return Classification(rules.unknown_tool, ("unknown_tool",))

    level = invocation.base
    reasons: list[str] = []

    if invocation.actuation_tier >= 4 and level < rules.vision_tier_minimum:
        level = rules.vision_tier_minimum
        reasons.append("vision_tier")

    if invocation.trust is Trust.TOOL_UNTRUSTED:
        raised = level.raised_by(rules.untrusted_taint_levels)
        if raised < rules.untrusted_taint_minimum:
            raised = rules.untrusted_taint_minimum
        if raised > level:
            level = raised
            reasons.append("untrusted_taint")

    if invocation.affected_count > rules.bulk_threshold:
        raised = level.raised_by(rules.bulk_levels)
        if raised > level:
            level = raised
            reasons.append("bulk")

    if _touches_sensitive_resource(invocation.parameters, rules):
        if level < Capability.L4:
            reasons.append("sensitive_resource")
        level = Capability.L4

    return Classification(level, tuple(reasons))


def _touches_sensitive_resource(parameters: Mapping[str, Any], rules: EscalationRules) -> bool:
    for key, value in _walk(parameters):
        if key in PATH_KEYS and any_match(value, list(rules.sensitive_paths)):
            return True
        host = _host_of(value)
        if host and any(domain_match(host, pattern) for pattern in rules.sensitive_domains):
            return True
    return False


def _walk(value: Any, key: str = "", depth: int = 0) -> Iterator[tuple[str, str]]:
    """Yield ``(nearest key, string value)`` pairs from a nested parameter tree."""
    if depth > 6:
        return
    if isinstance(value, str):
        yield key, value
    elif isinstance(value, Mapping):
        for child_key, child in value.items():
            yield from _walk(child, str(child_key).casefold(), depth + 1)
    elif isinstance(value, Sequence):
        for child in value:
            yield from _walk(child, key, depth + 1)


def _host_of(value: str) -> str | None:
    if "://" not in value:
        return None
    return urlsplit(value).hostname
