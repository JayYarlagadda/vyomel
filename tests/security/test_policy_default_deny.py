"""Policy evaluation and default-deny (FR-302, FR-309).

Every test here is a question of the form "what happens when the policy does
*not* say?" — because that is the case a policy engine gets wrong.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vyomel.core.errors import ConfigError
from vyomel.core.types import Capability, Decision
from vyomel.security.policy import (
    DENY_ALL,
    PolicyRequest,
    PolicyStore,
    load_policy,
    parse_policy,
    safe_load_policy,
    variables_for,
)

VARIABLES = variables_for(Path("D:/Vyomel/.vyomel/scratch"), Path("D:/Vyomel/.vyomel"))


def shipped() -> object:
    return load_policy(Path("config/policy.yaml"), variables=VARIABLES)


def request(tool: str, level: Capability, **parameters: object) -> PolicyRequest:
    return PolicyRequest(tool=tool, level=level, parameters=parameters)


@pytest.mark.req("FR-302")
def test_the_shipped_policy_parses() -> None:
    policy = shipped()
    assert policy.version >= 1  # type: ignore[attr-defined]
    assert policy.rules  # type: ignore[attr-defined]


@pytest.mark.req("FR-302")
def test_a_level_with_no_default_and_no_rule_is_denied() -> None:
    policy = parse_policy(
        """
        version: 1
        defaults:
          L0: allow
        rules:
          - id: nothing
            match: { tool: "never.matches" }
            decision: allow
        """
    )
    verdict = policy.evaluate(request("fs.read_file", Capability.L2, path="D:/x"))
    assert verdict.decision is Decision.DENY
    assert verdict.rule_id == "default:deny"


@pytest.mark.req("FR-302")
def test_an_unknown_tool_is_never_allowed() -> None:
    """Classification sends unknown tools to L4; policy must not wave them through."""
    policy = shipped()
    verdict = policy.evaluate(request("mystery.tool", Capability.L4))  # type: ignore[attr-defined]
    assert verdict.decision is not Decision.ALLOW


@pytest.mark.req("FR-302")
def test_deny_rules_win_over_matching_allow_rules() -> None:
    policy = parse_policy(
        """
        version: 1
        defaults:
          L0: allow
        rules:
          - id: allow-all-reads
            match: { tool: "fs.read_file" }
            decision: allow
          - id: protect-keys
            match: { args: { path: ["**/.ssh/**"] } }
            decision: deny
            reason: "Credential-bearing path"
        """
    )
    # Declared after the allow rule, and still wins: deny is evaluated first,
    # so rule ordering cannot be used to smuggle an exception past a deny.
    verdict = policy.evaluate(
        request("fs.read_file", Capability.L0, path="C:/Users/me/.ssh/id_rsa")
    )
    assert verdict.decision is Decision.DENY
    assert verdict.rule_id == "protect-keys"
    assert verdict.reason == "Credential-bearing path"


@pytest.mark.req("FR-302")
def test_the_shipped_policy_denies_credential_paths() -> None:
    policy = shipped()
    for path in ("C:/Users/me/.ssh/id_rsa", "D:/app/.env", "D:/certs/key.pem"):
        verdict = policy.evaluate(request("fs.read_file", Capability.L0, path=path))  # type: ignore[attr-defined]
        assert verdict.decision is Decision.DENY, path


@pytest.mark.req("FR-302")
@pytest.mark.req("FR-309")
def test_an_allow_rule_does_not_cover_an_action_above_its_declared_level() -> None:
    """The scoping property that makes 'trust this workflow' (FR-310) safe."""
    policy = parse_policy(
        """
        version: 1
        defaults:
          L2: confirm
          L3: confirm
        rules:
          - id: trusted-grading
            match: { workflow: "cs151_grading", tool: "desktop.*" }
            decision: allow
            max_level: L2
        """
    )
    covered = PolicyRequest(tool="desktop.set_field", level=Capability.L2, workflow="cs151_grading")
    assert policy.evaluate(covered).decision is Decision.ALLOW

    escalated = PolicyRequest(
        tool="desktop.set_field", level=Capability.L3, workflow="cs151_grading"
    )
    assert policy.evaluate(escalated).decision is Decision.CONFIRM


@pytest.mark.req("FR-302")
def test_an_expired_rule_is_inert() -> None:
    policy = parse_policy(
        """
        version: 1
        defaults:
          L2: confirm
        rules:
          - id: temporary
            match: { tool: "fs.*" }
            decision: allow
            expires: "2020-01-01"
        """
    )
    assert policy.evaluate(request("fs.write_file", Capability.L2)).decision is Decision.CONFIRM


@pytest.mark.req("FR-302")
def test_a_rule_whose_args_are_absent_does_not_match() -> None:
    policy = parse_policy(
        """
        version: 1
        defaults:
          L2: confirm
        rules:
          - id: scratch-writes
            match: { tool: "fs.write_file", args: { path: ["${scratch_dir}/**"] } }
            decision: allow
        """,
        variables=VARIABLES,
    )
    assert policy.evaluate(request("fs.write_file", Capability.L2)).decision is Decision.CONFIRM
    inside = request("fs.write_file", Capability.L2, path="D:/Vyomel/.vyomel/scratch/out.txt")
    assert policy.evaluate(inside).decision is Decision.ALLOW


@pytest.mark.req("FR-302")
@pytest.mark.parametrize(
    "document",
    [
        "",  # empty file
        "just a string",
        "version: not-an-int",
        "version: 1\ndefaults: {L0: maybe}",
        "version: 1\nrules: [{id: x, match: {tool: 'fs.*'}, decision: allow-ish}]",
        "version: 1\nrules: [{match: {tool: 'fs.*'}, decision: allow}]",  # no id
        "version: 1\nrules: [{id: x, decision: allow}]",  # no match
        "version: 1\nrules: [{id: x, match: {}, decision: allow}]",  # matches everything
        "version: 1\nrules: [{id: x, match: {tool: a}, decision: allow, expires: soon}]",
        "version: 1\nrules: not-a-list",
        "version: 1\nrules: [{id: dup, match: {tool: a}, decision: deny}, "
        "{id: dup, match: {tool: b}, decision: deny}]",
        "version: 1\nescalation: {bulk_threshold: -3}",
        "version: 1\negress: {deny_by_default: sure}",
        "version: 1\ndefaults: {L9: allow}",
    ],
)
def test_a_policy_that_is_not_fully_understood_is_rejected(document: str) -> None:
    with pytest.raises(ConfigError):
        parse_policy(document)


@pytest.mark.req("FR-302")
def test_an_unreadable_policy_denies_everything(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    policy = safe_load_policy(missing)
    assert policy is DENY_ALL
    for level in Capability:
        assert policy.evaluate(request("fs.read_file", level)).decision is Decision.DENY


@pytest.mark.req("FR-302")
def test_a_malformed_policy_denies_everything_rather_than_partially_applying(
    tmp_path: Path,
) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(
        # A truncated edit: the deny rule that protected keys is gone, and the
        # remaining document is invalid. Applying "what parsed" would be worse
        # than applying nothing.
        "version: 1\ndefaults: {L0: allow}\nrules: [{id: broken, match: {tool:",
        encoding="utf-8",
    )
    assert safe_load_policy(path) is DENY_ALL


@pytest.mark.req("FR-302")
def test_the_store_reloads_when_the_file_changes(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text("version: 1\ndefaults: {L2: confirm}\n", encoding="utf-8")
    store = PolicyStore(path)
    first = store.get().evaluate(request("fs.write_file", Capability.L2))
    assert first.decision is Decision.CONFIRM

    path.write_text(
        "version: 2\ndefaults: {L2: deny}\n" + "# padding to change the size\n", encoding="utf-8"
    )
    reloaded = store.get()
    assert reloaded.version == 2
    assert reloaded.evaluate(request("fs.write_file", Capability.L2)).decision is Decision.DENY


@pytest.mark.req("FR-302")
def test_a_decision_is_attributable_to_a_policy_version_and_hash() -> None:
    policy = shipped()
    verdict = policy.evaluate(request("fs.read_file", Capability.L0, path="D:/Vyomel/README.md"))  # type: ignore[attr-defined]
    payload = verdict.to_payload()
    assert payload["policy_hash"] == policy.hash  # type: ignore[attr-defined]
    assert payload["rule_id"]
    assert payload["decision"] == verdict.decision.value
