"""L0 verification is pass-by-result; ≥ L2 without a method is UNVERIFIED (FR-402)."""

from __future__ import annotations

import pytest

from vyomel.core.types import Capability, VerifyOutcome
from vyomel.verify.engine import verify_result


@pytest.mark.req("FR-402")
def test_l0_without_postconditions_passes() -> None:
    report = verify_result(capability=Capability.L0, postconditions=[], result={"ok": True})
    assert report.outcome is VerifyOutcome.PASS


@pytest.mark.req("FR-402")
def test_l2_without_a_method_is_never_success() -> None:
    report = verify_result(capability=Capability.L2, postconditions=[], result={"ok": True})
    assert report.outcome is VerifyOutcome.NO_METHOD
    assert report.outcome is not VerifyOutcome.PASS


@pytest.mark.req("FR-402")
def test_a_failed_check_is_fail_not_unverified() -> None:
    report = verify_result(
        capability=Capability.L1,
        postconditions=[{"type": "value_equals", "field": "value", "expected": 87}],
        result={"value": 78},
    )
    assert report.outcome is VerifyOutcome.FAIL


@pytest.mark.req("FR-402")
def test_fail_wins_over_a_missing_method() -> None:
    report = verify_result(
        capability=Capability.L2,
        postconditions=[
            {"type": "value_equals", "field": "n", "expected": 1},
            {"type": "llm_judge", "expected": "ok"},
        ],
        result={"n": 0},
    )
    assert report.outcome is VerifyOutcome.FAIL
