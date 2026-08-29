"""The CLI approval and policy commands.

These assert the CLI's half of the contract: which endpoint it calls, what it
sends, and what exit code it returns. The endpoints themselves are covered
against a real database in ``tests/api``. Stubbing the transport here is what
keeps this suite from re-testing FastAPI, while still catching the mistakes that
actually happen in a CLI -- a wrong path, a dropped flag, an edit that silently
loses the parameters it was supposed to preserve.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

import astra.cli.client as client_module
from astra.cli.main import app

runner = CliRunner()


class Recorder:
    """Stands in for one HTTP round trip, remembering what was asked."""

    def __init__(self, responses: dict[tuple[str, str], Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        console: Any,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append({"method": method, "path": path, "params": params, "json": json})
        try:
            return self.responses[(method, path)]
        except KeyError:  # pragma: no cover - a test asked for an unstubbed call
            raise AssertionError(f"unexpected call: {method} {path}") from None


def approval(**overrides: Any) -> dict[str, Any]:
    body = {
        "id": "01APPROVAL",
        "task_id": "01TASK",
        "action_id": "01ACTION",
        "capability_level": "L3",
        "status": "PENDING",
        "summary": "Notify dean@example.edu",
        "presented": {"tool": "test.notify", "parameters": {"recipient": "a@b.com", "body": "hi"}},
        "blast_radius": {"reversible": False},
        "expires_at": "2026-08-28T23:00:00Z",
        "created_at": "2026-08-28T22:00:00Z",
    }
    return body | overrides


Install = Callable[[dict[tuple[str, str], Any]], Recorder]


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Install:
    def install(responses: dict[tuple[str, str], Any]) -> Recorder:
        rec = Recorder(responses)
        monkeypatch.setattr(client_module, "request", rec)
        return rec

    return install


def test_approvals_lists_pending_by_default(recorder: Install) -> None:
    rec = recorder({("GET", "/v1/approvals"): {"items": [approval()]}})

    result = runner.invoke(app, ["approvals"])

    assert result.exit_code == 0, result.output
    assert rec.calls[0]["params"]["status"] == "PENDING"
    assert "01APPROVAL" in result.output


def test_approvals_all_drops_the_status_filter(recorder: Install) -> None:
    rec = recorder({("GET", "/v1/approvals"): {"items": []}})

    result = runner.invoke(app, ["approvals", "--status", "all"])

    assert result.exit_code == 0
    assert "status" not in rec.calls[0]["params"]
    assert "Nothing is waiting" in result.output


def test_approve_sends_a_plain_decision(recorder: Install) -> None:
    rec = recorder(
        {
            ("POST", "/v1/approvals/01APPROVAL/decide"): approval(status="APPROVED"),
        }
    )

    result = runner.invoke(app, ["approve", "01APPROVAL", "--by", "user:test"])

    assert result.exit_code == 0, result.output
    assert rec.calls[0]["json"] == {"decision": "APPROVED", "decided_by": "user:test"}
    assert "APPROVED" in result.output


def test_reject_carries_the_reason(recorder: Install) -> None:
    rec = recorder({("POST", "/v1/approvals/01APPROVAL/decide"): approval(status="REJECTED")})

    result = runner.invoke(app, ["reject", "01APPROVAL", "--reason", "wrong person"])

    assert result.exit_code == 0, result.output
    assert rec.calls[0]["json"]["decision"] == "REJECTED"
    assert rec.calls[0]["json"]["reason"] == "wrong person"


def test_modify_merges_overrides_onto_the_presented_parameters(recorder: Install) -> None:
    """An edit changes one field. Dropping the rest would silently rewrite the
    invocation the user was shown."""
    rec = recorder(
        {
            ("GET", "/v1/approvals/01APPROVAL"): approval(),
            ("POST", "/v1/approvals/01APPROVAL/decide"): approval(status="MODIFIED"),
        }
    )

    result = runner.invoke(app, ["modify", "01APPROVAL", "--set", "recipient=c@d.com"])

    assert result.exit_code == 0, result.output
    sent = rec.calls[1]["json"]
    assert sent["decision"] == "MODIFIED"
    assert sent["parameters"] == {"recipient": "c@d.com", "body": "hi"}


def test_modify_parses_values_as_json_when_it_can(recorder: Install) -> None:
    rec = recorder(
        {
            ("GET", "/v1/approvals/01APPROVAL"): approval(),
            ("POST", "/v1/approvals/01APPROVAL/decide"): approval(status="MODIFIED"),
        }
    )

    result = runner.invoke(app, ["modify", "01APPROVAL", "--set", "value=85", "--set", "body=done"])

    assert result.exit_code == 0, result.output
    parameters = rec.calls[1]["json"]["parameters"]
    assert parameters["value"] == 85
    assert parameters["body"] == "done"


def test_modify_requires_an_override() -> None:
    result = runner.invoke(app, ["modify", "01APPROVAL"])
    assert result.exit_code == 1
    assert "--set" in result.output


def test_modify_rejects_a_malformed_override(recorder: Install) -> None:
    recorder({("GET", "/v1/approvals/01APPROVAL"): approval()})
    result = runner.invoke(app, ["modify", "01APPROVAL", "--set", "recipient"])
    assert result.exit_code == 1
    assert "key=value" in result.output


def test_audit_verify_reports_an_intact_chain(recorder: Install) -> None:
    recorder({("POST", "/v1/audit/verify"): {"ok": True, "rows": 42}})

    result = runner.invoke(app, ["audit", "verify"])

    assert result.exit_code == 0
    assert "42" in result.output


def test_audit_verify_exits_nonzero_on_a_broken_chain(recorder: Install) -> None:
    recorder(
        {
            ("POST", "/v1/audit/verify"): {
                "ok": False,
                "rows": 42,
                "first_divergence_id": 17,
                "detail": "row contents do not match the recorded hash",
            }
        }
    )

    result = runner.invoke(app, ["audit", "verify"])

    assert result.exit_code == 1
    assert "17" in result.output


def test_audit_tail_passes_filters_through(recorder: Install) -> None:
    rec = recorder(
        {
            ("GET", "/v1/audit"): {
                "items": [
                    {
                        "id": 3,
                        "occurred_at": "2026-08-28T22:00:00Z",
                        "actor": "system:gate",
                        "event_type": "approval.requested",
                        "capability_level": "L3",
                        "task_id": "01TASK",
                    }
                ]
            }
        }
    )

    result = runner.invoke(app, ["audit", "tail", "--task", "01TASK", "--limit", "5"])

    assert result.exit_code == 0, result.output
    assert rec.calls[0]["params"] == {"limit": 5, "task_id": "01TASK"}
    assert "approval.requested" in result.output


def test_policy_test_renders_the_decision(recorder: Install) -> None:
    rec = recorder(
        {
            ("POST", "/v1/policy/test"): {
                "tool": "fs.read_file",
                "capability_level": "L4",
                "escalation_reasons": ["path looks like a credential store"],
                "decision": "DENY",
                "rule_id": "protected-paths",
                "reason": "denied by rule",
                "policy_version": 1,
                "policy_hash": "abcdef123456",
            }
        }
    )

    result = runner.invoke(app, ["policy", "test", "fs.read_file", '{"path": "D:/x/.env"}'])

    assert result.exit_code == 0, result.output
    assert rec.calls[0]["json"] == {
        "tool": "fs.read_file",
        "parameters": {"path": "D:/x/.env"},
    }
    assert "DENY" in result.output
    assert "protected-paths" in result.output


def test_policy_test_rejects_malformed_json() -> None:
    result = runner.invoke(app, ["policy", "test", "fs.read_file", "{not json}"])
    assert result.exit_code == 1
    assert "not valid JSON" in result.output


def test_policy_show_renders_defaults_and_rules(recorder: Install) -> None:
    recorder(
        {
            ("GET", "/v1/policy"): {
                "version": 1,
                "policy_hash": "deadbeef",
                "source": "config/policy.yaml",
                "defaults": {"L0": "ALLOW", "L4": "CONFIRM"},
                "rules": [
                    {
                        "id": "protected-paths",
                        "decision": "DENY",
                        "tool": "fs.*",
                        "level": None,
                        "max_level": None,
                        "args": {},
                        "reason": "credential stores are never readable",
                        "expires": None,
                    }
                ],
                "egress_deny_by_default": True,
                "egress_allow_domains": [],
            }
        }
    )

    result = runner.invoke(app, ["policy", "show"])

    assert result.exit_code == 0, result.output
    assert "deadbeef" in result.output
    assert "protected-paths" in result.output


def test_cancel_posts_compensate_true_by_default(recorder: Install) -> None:
    rec = recorder(
        {
            ("POST", "/v1/tasks/01TASK/cancel"): {
                "task_id": "01TASK",
                "status": "CANCELLED",
                "compensated": [],
                "irreversible": [],
                "still_running": [],
                "failed": [],
            }
        }
    )

    result = runner.invoke(app, ["cancel", "01TASK"])

    assert result.exit_code == 0, result.output
    assert rec.calls[0]["json"] == {"compensate": True}
    assert "CANCELLED" in result.output


def test_cancel_can_skip_compensation(recorder: Install) -> None:
    rec = recorder(
        {
            ("POST", "/v1/tasks/01TASK/cancel"): {
                "task_id": "01TASK",
                "status": "CANCELLED",
                "compensated": [],
                "irreversible": [
                    {
                        "action_id": "01ACTION",
                        "tool": "git.push",
                        "summary": "git.push already ran and cannot be undone",
                    }
                ],
                "still_running": [],
                "failed": [],
            }
        }
    )

    result = runner.invoke(app, ["cancel", "01TASK", "--no-compensate"])

    assert result.exit_code == 0, result.output
    assert rec.calls[0]["json"] == {"compensate": False}
    assert "Could not undo" in result.output
    assert "git.push" in result.output


def test_an_unreachable_api_is_reported_as_such(monkeypatch: pytest.MonkeyPatch) -> None:
    """The most common CLI failure by far. It must not be a traceback."""
    import httpx

    class Boom:
        def __enter__(self) -> Boom:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def request(self, *_: object, **__: object) -> None:
            raise httpx.ConnectError("nope")

    monkeypatch.setattr(client_module, "client", lambda *a, **k: Boom())

    from rich.console import Console

    console = Console()
    with pytest.raises(typer.Exit) as caught:
        client_module.request(console, "GET", "/v1/approvals")
    assert caught.value.exit_code == 2
