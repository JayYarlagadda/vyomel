"""L4 is never auto-approved (FR-306).

This is the one guarantee in Vyomel with no configuration path, no environment
variable, and no CLI flag. The test does not check that the shipped policy is
sensible — it checks that *no* policy, including one written by an adversary who
knows the implementation, can produce ``ALLOW`` for an L4 action.

Two enforcement points are exercised, and they are independent on purpose:

1. The loader corrects ``defaults.L4: allow``.
2. Evaluation raises :class:`PolicyInvariantViolation` if an ``ALLOW`` ever
   reaches an L4 request, whatever produced it.

A failure here is not a bug to triage later. It means Vyomel can spend money or
change credentials without asking.
"""

from __future__ import annotations

import pytest
import yaml
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from vyomel.core.errors import ConfigError, PolicyInvariantViolation
from vyomel.core.types import Capability, Decision
from vyomel.security.policy import PolicyRequest, parse_policy

L4_REQUEST = PolicyRequest(
    tool="payment.transfer",
    level=Capability.L4,
    parameters={"amount": 5_000, "path": "C:/Users/me/.ssh/id_rsa", "to": "someone@example.com"},
    workflow="trusted_workflow",
)

# Every knob an attacker might reach for, including the ones that look harmless.
_DECISIONS = st.sampled_from(["allow", "confirm", "deny", "ALLOW", "Allow"])
_LEVEL_NAMES = st.sampled_from(["L0", "L1", "L2", "L3", "L4"])
_TOOL_PATTERNS = st.sampled_from(
    ["*", "**", "payment.*", "payment.transfer", "*.transfer", "fs.*", "?ayment.transfer"]
)


@st.composite
def adversarial_policy(draw: st.DrawFn) -> str:
    defaults = {
        name: draw(_DECISIONS)
        for name in draw(st.lists(_LEVEL_NAMES, min_size=0, max_size=5, unique=True))
    }
    rules = []
    for index in range(draw(st.integers(min_value=0, max_value=4))):
        rule: dict[str, object] = {
            "id": f"rule-{index}",
            "match": {"tool": draw(_TOOL_PATTERNS)},
            "decision": draw(_DECISIONS),
        }
        if draw(st.booleans()):
            rule["level"] = draw(_LEVEL_NAMES)
        if draw(st.booleans()):
            rule["max_level"] = draw(_LEVEL_NAMES)
        if draw(st.booleans()):
            rule["match"] = {"args": {"path": ["**"]}}
        if draw(st.booleans()):
            rule["match"] = {"workflow": "trusted_workflow"}
        rules.append(rule)

    document: dict[str, object] = {"version": 1, "defaults": defaults, "rules": rules}
    if draw(st.booleans()):
        # Redefining the lattice itself: unknown keys must not become policy.
        document["capabilities"] = {"L4": "L0"}
    if draw(st.booleans()):
        document["escalation"] = {"unknown_tool": draw(_LEVEL_NAMES)}
    return yaml.safe_dump(document)


@pytest.mark.req("FR-306")
@settings(max_examples=400, suppress_health_check=[HealthCheck.too_slow])
@given(document=adversarial_policy())
def test_no_policy_can_auto_approve_l4(document: str) -> None:
    try:
        policy = parse_policy(document)
    except ConfigError:
        return  # rejected outright, which is also a pass
    try:
        verdict = policy.evaluate(L4_REQUEST)
    except PolicyInvariantViolation:
        return  # the invariant fired, which is the point
    assert verdict.decision is not Decision.ALLOW, document


@pytest.mark.req("FR-306")
def test_the_loader_corrects_an_l4_allow_default() -> None:
    policy = parse_policy("version: 1\ndefaults: {L0: allow, L4: allow}\n")
    assert policy.defaults[Capability.L4] is Decision.CONFIRM
    assert policy.evaluate(L4_REQUEST).decision is Decision.CONFIRM


@pytest.mark.req("FR-306")
def test_an_explicit_allow_rule_at_l4_raises_rather_than_allowing() -> None:
    policy = parse_policy(
        """
        version: 1
        defaults: {L4: confirm}
        rules:
          - id: sneaky
            match: { tool: "payment.*" }
            decision: allow
        """
    )
    with pytest.raises(PolicyInvariantViolation):
        policy.evaluate(L4_REQUEST)


@pytest.mark.req("FR-306")
def test_l4_may_still_be_denied() -> None:
    """DENY is stricter than CONFIRM, so it cannot be an invariant violation."""
    policy = parse_policy(
        """
        version: 1
        defaults: {L4: confirm}
        rules:
          - id: protected
            match: { args: { path: ["**/.ssh/**"] } }
            decision: deny
        """
    )
    assert policy.evaluate(L4_REQUEST).decision is Decision.DENY


@pytest.mark.req("FR-306")
def test_the_shipped_policy_confirms_l4() -> None:
    from pathlib import Path

    from vyomel.security.policy import load_policy, variables_for

    policy = load_policy(
        Path("config/policy.yaml"),
        variables=variables_for(Path("D:/Vyomel/.vyomel/scratch"), Path("D:/Vyomel/.vyomel")),
    )
    assert policy.defaults[Capability.L4] is Decision.CONFIRM
    harmless_l4 = PolicyRequest(tool="payment.transfer", level=Capability.L4, parameters={})
    assert policy.evaluate(harmless_l4).decision is Decision.CONFIRM
