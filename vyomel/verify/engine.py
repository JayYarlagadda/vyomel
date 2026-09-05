"""Postcondition checks.

A tool's word is never enough. After execution the engine re-observes what it
can and asserts the declared postconditions. Three outcomes, never a fourth:

- ``PASS`` — every check observed the expected state.
- ``FAIL`` — at least one check observed a contradiction. The action fails.
- ``NO_METHOD`` — a check has no observation path (or none was declared at
  ≥ L2). The action becomes ``UNVERIFIED``, never ``SUCCEEDED`` (FR-402).

``value_equals``, ``file_exists``, and ``file_hash`` re-observe. The other
registered types (``element_exists``, ``api_readback``, ``llm_judge``) are
dispatched so FR-403 is not a catalog of names: an unknown type is ``NO_METHOD``
rather than a silent pass, and a type whose observation path does not exist yet
is the same. Optimism is the bug this module exists to prevent.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vyomel.core.errors import ToolError
from vyomel.core.ids import file_digest
from vyomel.core.types import Capability, VerifyOutcome
from vyomel.tools.sandbox import resolve_in_sandbox

# Names FR-403 requires the engine to recognize. A missing name here is a
# missing dispatcher branch, which the type-coverage test will fail on.
SUPPORTED_VERIFIERS: frozenset[str] = frozenset(
    {
        "value_equals",
        "element_exists",
        "file_exists",
        "file_hash",
        "api_readback",
        "llm_judge",
    }
)


@dataclass(frozen=True, slots=True)
class Verification:
    """One postcondition check, with the evidence that produced the outcome."""

    outcome: VerifyOutcome
    verifier: str
    expected: Any = None
    observed: Any = None
    observation_tier: int = 1
    evidence_ref: str | None = None
    latency_ms: int = 0


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """Aggregate of every check on one action.

    ``FAIL`` wins over ``NO_METHOD``: a contradiction is more informative than a
    missing method, and the action should fail rather than sit in UNVERIFIED.
    """

    outcome: VerifyOutcome
    checks: tuple[Verification, ...]

    @property
    def verifier(self) -> str:
        if len(self.checks) == 1:
            return self.checks[0].verifier
        return "aggregate"


def verify_result(
    *,
    capability: Capability,
    postconditions: list[dict[str, Any]] | None,
    result: dict[str, Any],
    allowed_roots: Sequence[Path] | None = None,
) -> VerificationReport:
    """Run every declared postcondition and aggregate.

    An empty list is a method only at L0/L1 (no world mutation to re-observe).
    At ≥ L2 it is ``NO_METHOD``.
    """
    checks = list(postconditions or [])
    if not checks:
        if capability >= Capability.L2:
            return VerificationReport(
                outcome=VerifyOutcome.NO_METHOD,
                checks=(
                    Verification(outcome=VerifyOutcome.NO_METHOD, verifier="none", observed=result),
                ),
            )
        return VerificationReport(
            outcome=VerifyOutcome.PASS,
            checks=(
                Verification(
                    outcome=VerifyOutcome.PASS, verifier="l0_result_present", observed=result
                ),
            ),
        )

    roots = list(allowed_roots or [])
    ran = tuple(_run_one(spec, result=result, allowed_roots=roots) for spec in checks)
    return VerificationReport(outcome=_aggregate(ran), checks=ran)


def _aggregate(checks: Sequence[Verification]) -> VerifyOutcome:
    if any(check.outcome is VerifyOutcome.FAIL for check in checks):
        return VerifyOutcome.FAIL
    if any(check.outcome is VerifyOutcome.NO_METHOD for check in checks):
        return VerifyOutcome.NO_METHOD
    return VerifyOutcome.PASS


def _run_one(
    spec: Mapping[str, Any], *, result: dict[str, Any], allowed_roots: Sequence[Path]
) -> Verification:
    started = time.perf_counter()
    name = _verifier_name(spec)
    tier = int(spec.get("tier") or spec.get("observation_tier") or 1)
    try:
        check = _DISPATCH.get(name, _unknown)(spec, result, allowed_roots)
    except Exception as exc:
        check = Verification(
            outcome=VerifyOutcome.FAIL,
            verifier=name or "unknown",
            expected=spec.get("expected"),
            observed=type(exc).__name__,
            observation_tier=tier,
        )
    elapsed_ms = max(0, int((time.perf_counter() - started) * 1000))
    return Verification(
        outcome=check.outcome,
        verifier=check.verifier,
        expected=check.expected,
        observed=check.observed,
        observation_tier=check.observation_tier or tier,
        evidence_ref=check.evidence_ref,
        latency_ms=elapsed_ms,
    )


def _verifier_name(spec: Mapping[str, Any]) -> str:
    raw = spec.get("type") or spec.get("verifier") or ""
    return str(raw).strip()


def _unknown(
    spec: Mapping[str, Any], result: dict[str, Any], allowed_roots: Sequence[Path]
) -> Verification:
    del result, allowed_roots
    return Verification(
        outcome=VerifyOutcome.NO_METHOD,
        verifier=_verifier_name(spec) or "unknown",
        expected=spec.get("expected"),
        observed="unrecognized verifier",
    )


def _unavailable(
    spec: Mapping[str, Any], result: dict[str, Any], allowed_roots: Sequence[Path]
) -> Verification:
    del result, allowed_roots
    name = _verifier_name(spec)
    return Verification(
        outcome=VerifyOutcome.NO_METHOD,
        verifier=name,
        expected=spec.get("expected"),
        observed=f"{name} has no observation path yet",
    )


def _value_equals(
    spec: Mapping[str, Any], result: dict[str, Any], allowed_roots: Sequence[Path]
) -> Verification:
    del allowed_roots
    field = spec.get("field")
    expected = spec.get("expected")
    if not isinstance(field, str) or not field:
        return Verification(
            outcome=VerifyOutcome.NO_METHOD,
            verifier="value_equals",
            expected=expected,
            observed="postcondition is missing field",
        )
    try:
        observed = _lookup(result, field)
    except KeyError:
        return Verification(
            outcome=VerifyOutcome.FAIL,
            verifier="value_equals",
            expected=expected,
            observed=None,
            evidence_ref=field,
        )
    passed = observed == expected
    return Verification(
        outcome=VerifyOutcome.PASS if passed else VerifyOutcome.FAIL,
        verifier="value_equals",
        expected=expected,
        observed=observed,
        evidence_ref=field,
    )


def _file_exists(
    spec: Mapping[str, Any], result: dict[str, Any], allowed_roots: Sequence[Path]
) -> Verification:
    del result
    path_value = spec.get("path")
    if not isinstance(path_value, str) or not path_value:
        return Verification(
            outcome=VerifyOutcome.NO_METHOD,
            verifier="file_exists",
            observed="postcondition is missing path",
        )
    try:
        target = resolve_in_sandbox(path_value, allowed_roots)
    except ToolError as exc:
        return Verification(
            outcome=VerifyOutcome.FAIL,
            verifier="file_exists",
            expected=path_value,
            observed=exc.user_message,
            evidence_ref=path_value,
        )
    kind = str(spec.get("kind") or "file")
    if kind == "dir":
        exists = target.is_dir()
    elif kind == "any":
        exists = target.exists()
    else:
        exists = target.is_file()
    return Verification(
        outcome=VerifyOutcome.PASS if exists else VerifyOutcome.FAIL,
        verifier="file_exists",
        expected=path_value,
        observed=str(target) if exists else None,
        evidence_ref=str(target),
    )


def _file_hash(
    spec: Mapping[str, Any], result: dict[str, Any], allowed_roots: Sequence[Path]
) -> Verification:
    del result
    path_value = spec.get("path")
    expected = spec.get("expected")
    if not isinstance(path_value, str) or not path_value:
        return Verification(
            outcome=VerifyOutcome.NO_METHOD,
            verifier="file_hash",
            expected=expected,
            observed="postcondition is missing path",
        )
    if not isinstance(expected, str) or not expected:
        return Verification(
            outcome=VerifyOutcome.NO_METHOD,
            verifier="file_hash",
            expected=expected,
            observed="postcondition is missing expected hash",
        )
    try:
        target = resolve_in_sandbox(path_value, allowed_roots)
    except ToolError as exc:
        return Verification(
            outcome=VerifyOutcome.FAIL,
            verifier="file_hash",
            expected=expected,
            observed=exc.user_message,
            evidence_ref=path_value,
        )
    if not target.is_file():
        return Verification(
            outcome=VerifyOutcome.FAIL,
            verifier="file_hash",
            expected=expected,
            observed=None,
            evidence_ref=str(target),
        )
    observed = file_digest(target)
    return Verification(
        outcome=VerifyOutcome.PASS if observed == expected else VerifyOutcome.FAIL,
        verifier="file_hash",
        expected=expected,
        observed=observed,
        evidence_ref=str(target),
    )


def _lookup(result: Mapping[str, Any], field: str) -> Any:
    current: Any = result
    for part in field.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise KeyError(field)
        current = current[part]
    return current


_Verifier = Callable[
    [Mapping[str, Any], dict[str, Any], Sequence[Path]],
    Verification,
]

_DISPATCH: dict[str, _Verifier] = {
    "value_equals": _value_equals,
    "file_exists": _file_exists,
    "file_hash": _file_hash,
    "element_exists": _unavailable,
    "api_readback": _unavailable,
    "llm_judge": _unavailable,
}
