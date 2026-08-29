"""Capability classification and escalation (FR-301).

The property that matters is one-directional: every rule in
docs/06-SECURITY-PERMISSIONS.md section 2 may raise a level and none may lower
one. A test suite that only checked specific cases would not notice a new rule
that accidentally introduced a downgrade path, so the last test here asserts the
direction over generated inputs.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from astra.core.types import Capability, Trust
from astra.security.capability import Classification, EscalationRules, Invocation, classify
from astra.security.policy import load_policy, variables_for

RULES = EscalationRules(
    sensitive_paths=("**/.ssh/**", "**/.env", "**/*.pem"),
    sensitive_domains=("*.chase.com",),
)


def _classify(**kwargs: object) -> Classification:
    return classify(Invocation(**kwargs), RULES)  # type: ignore[arg-type]


@pytest.mark.req("FR-301")
def test_base_level_passes_through_when_nothing_escalates() -> None:
    result = _classify(tool="fs.read_file", base=Capability.L0, parameters={"path": "C:/a/b.txt"})
    assert result.level is Capability.L0
    assert result.reasons == ()


@pytest.mark.req("FR-301")
@pytest.mark.req("FR-302")
def test_an_unclassifiable_invocation_is_l4_not_l0() -> None:
    """Rule 5. 'Unclassified' must mean maximum caution, never 'probably fine'."""
    result = _classify(tool="mystery.tool", base=None, parameters={})
    assert result.level is Capability.L4
    assert result.reasons == ("unknown_tool",)


@pytest.mark.req("FR-301")
def test_vision_tier_actuation_raises_to_l2() -> None:
    result = _classify(tool="desktop.click", base=Capability.L1, actuation_tier=4)
    assert result.level is Capability.L2
    assert "vision_tier" in result.reasons


@pytest.mark.req("FR-301")
def test_vision_tier_does_not_lower_an_already_higher_level() -> None:
    result = _classify(tool="desktop.click", base=Capability.L3, actuation_tier=4)
    assert result.level is Capability.L3
    assert result.reasons == ()


@pytest.mark.req("FR-301")
def test_untrusted_parameters_raise_one_level_with_an_l2_floor() -> None:
    tainted = _classify(tool="fs.read_file", base=Capability.L0, trust=Trust.TOOL_UNTRUSTED)
    assert tainted.level is Capability.L2  # +1 would be L1; the floor lifts it
    assert "untrusted_taint" in tainted.reasons

    higher = _classify(tool="email.send", base=Capability.L3, trust=Trust.TOOL_UNTRUSTED)
    assert higher.level is Capability.L4


@pytest.mark.req("FR-301")
def test_bulk_operations_raise_one_level() -> None:
    modest = _classify(tool="fs.delete", base=Capability.L2, affected_count=10)
    assert modest.level is Capability.L2

    bulk = _classify(tool="fs.delete", base=Capability.L2, affected_count=11)
    assert bulk.level is Capability.L3
    assert "bulk" in bulk.reasons


@pytest.mark.req("FR-301")
@pytest.mark.parametrize(
    "path",
    [
        "C:/Users/me/.ssh/id_rsa",
        "D:/proj/.env",
        "D:/certs/server.pem",
        # Windows separators and casing must not be an escape hatch.
        "C:\\Users\\me\\.SSH\\known_hosts",
    ],
)
def test_a_sensitive_path_forces_l4_regardless_of_the_tool(path: str) -> None:
    result = _classify(tool="fs.read_file", base=Capability.L0, parameters={"path": path})
    assert result.level is Capability.L4
    assert "sensitive_resource" in result.reasons


@pytest.mark.req("FR-301")
def test_a_sensitive_domain_forces_l4() -> None:
    result = _classify(
        tool="browser.click",
        base=Capability.L1,
        parameters={"url": "https://secure.chase.com/transfer"},
    )
    assert result.level is Capability.L4


@pytest.mark.req("FR-301")
def test_path_globs_are_not_matched_against_arbitrary_prose() -> None:
    """A note that mentions ``.env`` is not an action against ``.env``."""
    result = _classify(
        tool="task.report",
        base=Capability.L0,
        parameters={"summary": "the .env file was not touched"},
    )
    assert result.level is Capability.L0


@pytest.mark.req("FR-301")
def test_the_shipped_policy_file_configures_escalation() -> None:
    """Guards against the rules silently falling back to code defaults."""
    from pathlib import Path

    policy = load_policy(
        Path("config/policy.yaml"),
        variables=variables_for(Path("D:/Astra/.astra/scratch"), Path("D:/Astra/.astra")),
    )
    rules = policy.escalation
    assert rules.unknown_tool is Capability.L4
    assert rules.bulk_threshold == 10
    assert any("ssh" in pattern for pattern in rules.sensitive_paths)

    result = classify(
        Invocation(
            tool="fs.read_file", base=Capability.L0, parameters={"path": "C:/Users/me/.ssh/config"}
        ),
        rules,
    )
    assert result.level is Capability.L4


@pytest.mark.req("FR-301")
@given(
    base=st.sampled_from(list(Capability)),
    tier=st.integers(min_value=1, max_value=4),
    trust=st.sampled_from(list(Trust)),
    affected=st.integers(min_value=0, max_value=5_000),
    path=st.text(max_size=40),
)
def test_escalation_never_lowers_a_level(
    base: Capability, tier: int, trust: Trust, affected: int, path: str
) -> None:
    result = classify(
        Invocation(
            tool="some.tool",
            base=base,
            parameters={"path": path},
            actuation_tier=tier,
            trust=trust,
            affected_count=affected,
        ),
        RULES,
    )
    assert result.level >= base
