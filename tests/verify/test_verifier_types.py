"""Every FR-403 verifier is a dispatcher branch, not a name in a comment."""

from __future__ import annotations

from pathlib import Path

import pytest

from vyomel.core.config import Settings
from vyomel.core.ids import digest_bytes
from vyomel.core.types import Capability, VerifyOutcome
from vyomel.tools.api.session import get_api, reset_api_sessions
from vyomel.tools.browser.session import get_fixture_session, reset_sessions
from vyomel.verify.engine import SUPPORTED_VERIFIERS, ObserveContext, verify_result


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
def test_llm_judge_still_no_method() -> None:
    report = verify_result(
        capability=Capability.L2,
        postconditions=[{"type": "llm_judge", "expected": "anything"}],
        result={"ok": True},
    )
    assert report.outcome is VerifyOutcome.NO_METHOD
    assert report.checks[0].verifier == "llm_judge"


@pytest.mark.req("FR-403")
def test_element_exists_requeries_browser_fixture(tmp_path: Path) -> None:
    reset_sessions()
    settings = Settings(
        env="test",
        workspace_root=tmp_path / ".vyomel",
        browser_backend="fixture",
        browser_fixtures_dir=Path("vyomel/tools/browser/fixtures"),
    )
    task_id = "verify-el-1"
    session = get_fixture_session(settings, task_id=task_id)
    session.open("fixture://form_app")
    report = verify_result(
        capability=Capability.L2,
        postconditions=[
            {
                "type": "element_exists",
                "surface": "browser",
                "role": "button",
                "name": "Submit application",
            }
        ],
        result={"clicked": True},
        observe=ObserveContext(task_id=task_id, settings=settings),
    )
    assert report.outcome is VerifyOutcome.PASS
    assert report.checks[0].verifier == "element_exists"
    assert report.checks[0].observed is not None


@pytest.mark.req("FR-403")
def test_element_exists_fails_when_missing(tmp_path: Path) -> None:
    reset_sessions()
    settings = Settings(
        env="test",
        workspace_root=tmp_path / ".vyomel",
        browser_backend="fixture",
        browser_fixtures_dir=Path("vyomel/tools/browser/fixtures"),
    )
    task_id = "verify-el-2"
    get_fixture_session(settings, task_id=task_id).open("fixture://form_app")
    report = verify_result(
        capability=Capability.L2,
        postconditions=[
            {
                "type": "element_exists",
                "surface": "browser",
                "role": "button",
                "name": "DoesNotExist",
            }
        ],
        result={},
        observe=ObserveContext(task_id=task_id, settings=settings),
    )
    assert report.outcome is VerifyOutcome.FAIL


@pytest.mark.req("FR-403")
def test_api_readback_rereads_calendar_event(tmp_path: Path) -> None:
    reset_api_sessions()
    settings = Settings(env="test", workspace_root=tmp_path / ".vyomel")
    task_id = "verify-api-1"
    api = get_api(settings, task_id=task_id)
    from datetime import UTC, datetime, timedelta

    start = datetime(2026, 9, 5, 15, 0, tzinfo=UTC)
    event = api.create_event(
        title="Interview",
        start=start,
        end=start + timedelta(hours=1),
        attendees=["a@b.com"],
    )
    report = verify_result(
        capability=Capability.L3,
        postconditions=[
            {
                "type": "api_readback",
                "resource": "calendar.event",
                "id": event.id,
                "field": "title",
                "expected": "Interview",
            }
        ],
        result={"event_id": event.id, "title": "Interview"},
        observe=ObserveContext(task_id=task_id, settings=settings),
    )
    assert report.outcome is VerifyOutcome.PASS
    assert report.checks[0].observed == "Interview"

    lying = verify_result(
        capability=Capability.L3,
        postconditions=[
            {
                "type": "api_readback",
                "resource": "calendar.event",
                "id": event.id,
                "field": "title",
                "expected": "Wrong Title",
            }
        ],
        result={"event_id": event.id, "title": "Wrong Title"},
        observe=ObserveContext(task_id=task_id, settings=settings),
    )
    assert lying.outcome is VerifyOutcome.FAIL


@pytest.mark.req("FR-403")
def test_unknown_verifier_is_no_method_not_a_pass() -> None:
    report = verify_result(
        capability=Capability.L1,
        postconditions=[{"type": "psychic_guess", "expected": True}],
        result={"ok": True},
    )
    assert report.outcome is VerifyOutcome.NO_METHOD
    assert report.outcome is not VerifyOutcome.PASS
