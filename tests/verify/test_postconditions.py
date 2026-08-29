"""Postconditions re-observe; they do not trust the tool result (FR-401, FR-404)."""

from __future__ import annotations

from pathlib import Path

import pytest

from astra.core.ids import digest_bytes
from astra.core.types import Capability, VerifyOutcome
from astra.verify.engine import verify_result


@pytest.mark.req("FR-401")
def test_file_hash_reads_the_file_not_the_result(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    target.write_text("actual", encoding="utf-8")
    claimed = digest_bytes(b"claimed")

    report = verify_result(
        capability=Capability.L1,
        postconditions=[{"type": "file_hash", "path": str(target), "expected": claimed}],
        result={"path": str(target), "sha256": claimed, "content": "claimed"},
        allowed_roots=[tmp_path],
    )
    assert report.outcome is VerifyOutcome.FAIL
    assert report.checks[0].observed == digest_bytes(b"actual")


@pytest.mark.req("FR-401")
def test_file_exists_accepts_directories_when_kind_is_dir(tmp_path: Path) -> None:
    folder = tmp_path / "dir"
    folder.mkdir()
    report = verify_result(
        capability=Capability.L2,
        postconditions=[{"type": "file_exists", "path": str(folder), "kind": "dir"}],
        result={},
        allowed_roots=[tmp_path],
    )
    assert report.outcome is VerifyOutcome.PASS

    as_file = verify_result(
        capability=Capability.L2,
        postconditions=[{"type": "file_exists", "path": str(folder)}],
        result={},
        allowed_roots=[tmp_path],
    )
    assert as_file.outcome is VerifyOutcome.FAIL


@pytest.mark.req("FR-401")
def test_file_exists_fails_when_the_file_is_missing(tmp_path: Path) -> None:
    report = verify_result(
        capability=Capability.L1,
        postconditions=[{"type": "file_exists", "path": str(tmp_path / "nope.txt")}],
        result={"path": str(tmp_path / "nope.txt")},
        allowed_roots=[tmp_path],
    )
    assert report.outcome is VerifyOutcome.FAIL


@pytest.mark.req("FR-401")
def test_a_path_outside_the_sandbox_fails_closed(tmp_path: Path) -> None:
    report = verify_result(
        capability=Capability.L1,
        postconditions=[{"type": "file_exists", "path": str(tmp_path / ".." / "outside.txt")}],
        result={},
        allowed_roots=[tmp_path],
    )
    assert report.outcome is VerifyOutcome.FAIL
    assert report.outcome is not VerifyOutcome.PASS
