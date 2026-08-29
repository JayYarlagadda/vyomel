"""Every FR-403 verifier is a dispatcher branch, not a name in a comment."""

from __future__ import annotations

from pathlib import Path

import pytest

from astra.core.ids import digest_bytes
from astra.core.types import Capability, VerifyOutcome
from astra.verify.engine import SUPPORTED_VERIFIERS, verify_result


@pytest.mark.req("FR-403")
def test_required_verifier_names_are_registered() -> None:
    assert {
        "value_equals",
        "element_exists",
        "file_exists",
        "file_hash",
        "api_readback",
        "llm_judge",
    } == SUPPORTED_VERIFIERS


@pytest.mark.req("FR-403")
def test_value_equals_passes_on_a_matching_field() -> None:
    report = verify_result(
        capability=Capability.L3,
        postconditions=[{"verifier": "value_equals", "field": "delivered_to", "expected": "a@b"}],
        result={"delivered_to": "a@b"},
    )
    assert report.outcome is VerifyOutcome.PASS
    assert report.checks[0].observed == "a@b"


@pytest.mark.req("FR-403")
def test_value_equals_fails_on_a_mismatch() -> None:
    report = verify_result(
        capability=Capability.L1,
        postconditions=[{"type": "value_equals", "field": "n", "expected": 87}],
        result={"n": 78},
    )
    assert report.outcome is VerifyOutcome.FAIL
    assert report.checks[0].observed == 78
    assert report.checks[0].expected == 87


@pytest.mark.req("FR-403")
def test_file_exists_and_file_hash_reobserve(tmp_path: Path) -> None:
    target = tmp_path / "grade.txt"
    body = "87"
    target.write_text(body, encoding="utf-8")
    expected = digest_bytes(body.encode("utf-8"))

    report = verify_result(
        capability=Capability.L1,
        postconditions=[
            {"type": "file_exists", "path": str(target)},
            {"type": "file_hash", "path": str(target), "expected": expected},
        ],
        result={"sha256": "this-hash-is-a-lie"},
        allowed_roots=[tmp_path],
    )
    assert report.outcome is VerifyOutcome.PASS
    assert report.checks[1].observed == expected
    assert report.checks[1].observed != "this-hash-is-a-lie"


@pytest.mark.req("FR-403")
def test_file_hash_catches_a_wrong_value_write(tmp_path: Path) -> None:
    target = tmp_path / "grade.txt"
    target.write_text("78", encoding="utf-8")
    claimed = digest_bytes(b"87")

    report = verify_result(
        capability=Capability.L1,
        postconditions=[{"type": "file_hash", "path": str(target), "expected": claimed}],
        result={"sha256": claimed},
        allowed_roots=[tmp_path],
    )
    assert report.outcome is VerifyOutcome.FAIL
    assert report.checks[0].observed == digest_bytes(b"78")


@pytest.mark.req("FR-403")
@pytest.mark.parametrize("name", ["element_exists", "api_readback", "llm_judge"])
def test_unavailable_observation_paths_are_no_method(name: str) -> None:
    report = verify_result(
        capability=Capability.L2,
        postconditions=[{"type": name, "expected": "anything"}],
        result={"ok": True},
    )
    assert report.outcome is VerifyOutcome.NO_METHOD
    assert report.checks[0].verifier == name


@pytest.mark.req("FR-403")
def test_unknown_verifier_is_no_method_not_a_pass() -> None:
    report = verify_result(
        capability=Capability.L1,
        postconditions=[{"type": "psychic_guess", "expected": True}],
        result={"ok": True},
    )
    assert report.outcome is VerifyOutcome.NO_METHOD
    assert report.outcome is not VerifyOutcome.PASS
