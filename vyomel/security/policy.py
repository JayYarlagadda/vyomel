"""Declarative policy evaluation (FR-302, FR-306, FR-309).

docs/06-SECURITY-PERMISSIONS.md section 3. Every action is evaluated before it
is dispatched, and the outcome is one of ``ALLOW``, ``CONFIRM``, or ``DENY``.

Design commitments, each of which exists because the alternative fails unsafely:

- **Default deny.** Falling off the end of evaluation is ``DENY``, not ``ALLOW``.
  A rule that does not exist cannot permit anything.
- **Malformed policy denies everything.** A parse error yields
  :data:`DENY_ALL` rather than a partially-applied ruleset, so a truncated or
  hostile edit cannot drop the one rule that was protecting something.
- **Rules are scoped by level, not trusted to be narrow.** ``level`` and
  ``max_level`` on a rule are *conditions*: if the action classified higher than
  the rule declares, the rule does not apply. An allow rule therefore cannot
  silently grow to cover an action more dangerous than the one it was written
  for — the case ADR-worthy enough that the roadmap calls it out for
  "trust this workflow" (FR-310).
- **No ALLOW at L4, ever** (:func:`_assert_l4_invariant`).

The L4 invariant deviates in one detail from the snippet in section 3 of the
security document, which raises unless the decision *is* ``CONFIRM``. Taken
literally that makes ``DENY`` — a strictly stronger outcome — an invariant
violation, so a protected-path deny on a credential file would crash the
dispatcher. The invariant enforced here is "L4 is never auto-approved", which is
what FR-306 actually requires; the document has been amended to match.
"""

from __future__ import annotations

import datetime as dt
import functools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from vyomel.core.config import Settings
from vyomel.core.errors import ConfigError, PolicyInvariantViolation
from vyomel.core.ids import content_hash
from vyomel.core.logging import get_logger
from vyomel.core.types import Capability, Decision
from vyomel.security.capability import EscalationRules
from vyomel.security.matching import any_match, domain_match, glob_match

log = get_logger(__name__)

DEFAULT_POLICY_PATH = Path("config/policy.yaml")


@dataclass(frozen=True, slots=True)
class Match:
    tool: str | None = None
    args: Mapping[str, Any] = field(default_factory=dict)
    workflow: str | None = None

    def matches(self, request: PolicyRequest) -> bool:
        if self.tool is not None and not glob_match(request.tool, self.tool):
            return False
        if self.workflow is not None and (
            request.workflow is None or not glob_match(request.workflow, self.workflow)
        ):
            return False
        for key, patterns in self.args.items():
            value = request.parameters.get(key)
            # An absent parameter is not a match. For a deny rule that is
            # correct (nothing to protect); for an allow rule it is what keeps
            # the rule from covering invocations its author never saw.
            if not isinstance(value, str) or not any_match(value, patterns):
                return False
        return True

    @property
    def is_empty(self) -> bool:
        return self.tool is None and self.workflow is None and not self.args


@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    match: Match
    decision: Decision
    level: Capability | None = None
    max_level: Capability | None = None
    show: tuple[str, ...] = ()
    reason: str | None = None
    expires: dt.date | None = None

    def applies(self, request: PolicyRequest, today: dt.date) -> bool:
        if self.expires is not None and today > self.expires:
            return False
        if self.level is not None and request.level > self.level:
            return False
        if self.max_level is not None and request.level > self.max_level:
            return False
        return self.match.matches(request)


@dataclass(frozen=True, slots=True)
class Egress:
    deny_by_default: bool = True
    allow_domains: tuple[str, ...] = ()

    def allows(self, host: str) -> bool:
        if any(domain_match(host, pattern) for pattern in self.allow_domains):
            return True
        return not self.deny_by_default


@dataclass(frozen=True, slots=True)
class PolicyRequest:
    tool: str
    level: Capability
    parameters: Mapping[str, Any] = field(default_factory=dict)
    workflow: str | None = None


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    decision: Decision
    rule_id: str
    reason: str
    level: Capability
    policy_version: int
    policy_hash: str
    show: tuple[str, ...] = ()

    @property
    def requires_approval(self) -> bool:
        return self.decision is Decision.CONFIRM

    def to_payload(self) -> dict[str, Any]:
        """Audit representation. Every decision is attributable to a policy version."""
        return {
            "decision": self.decision.value,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "level": self.level.value,
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
        }


@dataclass(frozen=True, slots=True)
class Policy:
    version: int
    defaults: Mapping[Capability, Decision]
    rules: tuple[Rule, ...]
    egress: Egress
    escalation: EscalationRules
    sensitivity: Mapping[str, Any]
    hash: str
    source: Path | None = None

    def evaluate(self, request: PolicyRequest, *, today: dt.date | None = None) -> PolicyDecision:
        """First matching deny, then first matching allow/confirm, then the level
        default, then deny."""
        moment = today or dt.datetime.now(tz=dt.UTC).date()

        for rule in self.rules:
            if rule.decision is Decision.DENY and rule.applies(request, moment):
                return self._decide(
                    request, rule.decision, rule.id, rule.reason or "denied by rule"
                )

        for rule in self.rules:
            if rule.decision is not Decision.DENY and rule.applies(request, moment):
                return self._decide(
                    request,
                    rule.decision,
                    rule.id,
                    rule.reason or f"{rule.decision.value.lower()} by rule",
                    show=rule.show,
                )

        default = self.defaults.get(request.level)
        if default is not None:
            return self._decide(request, default, f"default:{request.level.value}", "level default")
        return self._decide(
            request, Decision.DENY, "default:deny", "no rule matched and no level default"
        )

    def _decide(
        self,
        request: PolicyRequest,
        decision: Decision,
        rule_id: str,
        reason: str,
        show: tuple[str, ...] = (),
    ) -> PolicyDecision:
        _assert_l4_invariant(request.level, decision, rule_id)
        return PolicyDecision(
            decision=decision,
            rule_id=rule_id,
            reason=reason,
            level=request.level,
            policy_version=self.version,
            policy_hash=self.hash,
            show=show,
        )


def _assert_l4_invariant(level: Capability, decision: Decision, rule_id: str) -> None:
    """No configuration, flag, or environment variable can auto-approve L4."""
    if level is Capability.L4 and decision is Decision.ALLOW:
        raise PolicyInvariantViolation(
            "L4 always requires explicit human confirmation",
            detail={"rule_id": rule_id, "level": level.value, "decision": decision.value},
        )


DENY_ALL = Policy(
    version=0,
    defaults=dict.fromkeys(Capability, Decision.DENY),
    rules=(),
    egress=Egress(deny_by_default=True),
    escalation=EscalationRules(),
    sensitivity={},
    hash="deny-all",
)


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

_DECISIONS = {"allow": Decision.ALLOW, "confirm": Decision.CONFIRM, "deny": Decision.DENY}


def load_policy(
    path: Path = DEFAULT_POLICY_PATH, *, variables: Mapping[str, str] | None = None
) -> Policy:
    """Parse and validate a policy file. Raises :class:`ConfigError` on anything
    it does not fully understand."""
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"policy file {path} is unreadable: {exc}") from exc
    return parse_policy(raw_text, variables=variables, source=path)


def parse_policy(
    raw_text: str,
    *,
    variables: Mapping[str, str] | None = None,
    source: Path | None = None,
) -> Policy:
    try:
        document = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"policy is not valid YAML: {exc}") from exc
    if not isinstance(document, Mapping):
        raise ConfigError("policy must be a mapping at the top level")

    version = document.get("version")
    if not isinstance(version, int):
        raise ConfigError("policy 'version' must be an integer")

    defaults = _parse_defaults(document.get("defaults"))
    rules = tuple(_parse_rules(document.get("rules"), variables or {}))
    escalation = _parse_escalation(document.get("escalation"), variables or {})
    egress = _parse_egress(document.get("egress"))
    sensitivity = document.get("sensitivity") or {}
    if not isinstance(sensitivity, Mapping):
        raise ConfigError("policy 'sensitivity' must be a mapping")

    return Policy(
        version=version,
        defaults=defaults,
        rules=rules,
        egress=egress,
        escalation=escalation,
        sensitivity=dict(sensitivity),
        hash=content_hash(raw_text),
        source=source,
    )


def safe_load_policy(
    path: Path = DEFAULT_POLICY_PATH, *, variables: Mapping[str, str] | None = None
) -> Policy:
    """Load, or fall back to :data:`DENY_ALL`.

    Used on the dispatch path, where refusing to start would strand in-flight
    tasks but proceeding without a policy is unthinkable.
    """
    try:
        return load_policy(path, variables=variables)
    except ConfigError as exc:
        log.critical("vyomel.security.policy_unusable", path=str(path), error=str(exc))
        return DENY_ALL


def _parse_defaults(raw: object) -> dict[Capability, Decision]:
    if raw is None:
        return {Capability.L4: Decision.CONFIRM}
    if not isinstance(raw, Mapping):
        raise ConfigError("policy 'defaults' must be a mapping of level to decision")
    defaults: dict[Capability, Decision] = {}
    for key, value in raw.items():
        level = _capability(key, where="defaults")
        defaults[level] = _decision(value, where=f"defaults.{key}")
    # A config that tries to relax L4 is corrected here and rejected again at
    # evaluation time. Two independent enforcement points, because this one is
    # the difference between a policy file and a liability.
    if defaults.get(Capability.L4) is Decision.ALLOW:
        log.critical("vyomel.security.l4_default_override_rejected")
        defaults[Capability.L4] = Decision.CONFIRM
    defaults.setdefault(Capability.L4, Decision.CONFIRM)
    return defaults


def _parse_rules(raw: object, variables: Mapping[str, str]) -> list[Rule]:
    if raw is None:
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise ConfigError("policy 'rules' must be a list")
    rules: list[Rule] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ConfigError(f"policy rule #{index} must be a mapping")
        rule_id = item.get("id")
        if not isinstance(rule_id, str) or not rule_id:
            raise ConfigError(f"policy rule #{index} needs a non-empty string 'id'")
        if rule_id in seen:
            raise ConfigError(f"duplicate policy rule id {rule_id!r}")
        seen.add(rule_id)
        rules.append(
            Rule(
                id=rule_id,
                match=_parse_match(item.get("match"), variables, rule_id),
                decision=_decision(item.get("decision"), where=f"rule {rule_id}"),
                level=_optional_capability(item.get("level"), where=f"rule {rule_id}"),
                max_level=_optional_capability(item.get("max_level"), where=f"rule {rule_id}"),
                show=_string_tuple(item.get("show"), where=f"rule {rule_id}.show"),
                reason=_optional_str(item.get("reason"), where=f"rule {rule_id}.reason"),
                expires=_optional_date(item.get("expires"), where=f"rule {rule_id}.expires"),
            )
        )
    return rules


def _parse_match(raw: object, variables: Mapping[str, str], rule_id: str) -> Match:
    if raw is None:
        raise ConfigError(f"policy rule {rule_id} has no 'match'")
    if not isinstance(raw, Mapping):
        raise ConfigError(f"policy rule {rule_id} 'match' must be a mapping")
    args_raw = raw.get("args") or {}
    if not isinstance(args_raw, Mapping):
        raise ConfigError(f"policy rule {rule_id} 'match.args' must be a mapping")
    args = {str(key): _substitute(value, variables) for key, value in args_raw.items()}
    match = Match(
        tool=_optional_str(raw.get("tool"), where=f"rule {rule_id}.match.tool"),
        args=args,
        workflow=_optional_str(raw.get("workflow"), where=f"rule {rule_id}.match.workflow"),
    )
    if match.is_empty:
        # A rule that matches everything is never what the author meant, and as
        # an allow rule it would quietly become the whole policy.
        raise ConfigError(f"policy rule {rule_id} matches every invocation")
    return match


def _parse_escalation(raw: object, variables: Mapping[str, str]) -> EscalationRules:
    if raw is None:
        return EscalationRules()
    if not isinstance(raw, Mapping):
        raise ConfigError("policy 'escalation' must be a mapping")
    defaults = EscalationRules()
    return EscalationRules(
        vision_tier_minimum=_optional_capability(
            raw.get("vision_tier_minimum"), where="escalation.vision_tier_minimum"
        )
        or defaults.vision_tier_minimum,
        untrusted_taint_levels=_non_negative_int(
            raw.get("untrusted_taint_levels"),
            defaults.untrusted_taint_levels,
            where="escalation.untrusted_taint_levels",
        ),
        untrusted_taint_minimum=_optional_capability(
            raw.get("untrusted_taint_minimum"), where="escalation.untrusted_taint_minimum"
        )
        or defaults.untrusted_taint_minimum,
        bulk_threshold=_non_negative_int(
            raw.get("bulk_threshold"), defaults.bulk_threshold, where="escalation.bulk_threshold"
        ),
        bulk_levels=_non_negative_int(
            raw.get("bulk_levels"), defaults.bulk_levels, where="escalation.bulk_levels"
        ),
        unknown_tool=_optional_capability(raw.get("unknown_tool"), where="escalation.unknown_tool")
        or defaults.unknown_tool,
        sensitive_paths=tuple(
            str(_substitute(pattern, variables))
            for pattern in _string_tuple(
                raw.get("sensitive_paths"), where="escalation.sensitive_paths"
            )
        ),
        sensitive_domains=_string_tuple(
            raw.get("sensitive_domains"), where="escalation.sensitive_domains"
        ),
    )


def _parse_egress(raw: object) -> Egress:
    if raw is None:
        return Egress()
    if not isinstance(raw, Mapping):
        raise ConfigError("policy 'egress' must be a mapping")
    deny_by_default = raw.get("deny_by_default", True)
    if not isinstance(deny_by_default, bool):
        raise ConfigError("policy 'egress.deny_by_default' must be a boolean")
    return Egress(
        deny_by_default=deny_by_default,
        allow_domains=_string_tuple(raw.get("allow_domains"), where="egress.allow_domains"),
    )


def _substitute(value: object, variables: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        for name, replacement in variables.items():
            value = value.replace(f"${{{name}}}", replacement)
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_substitute(item, variables) for item in value]
    return value


def _capability(value: object, *, where: str) -> Capability:
    level = _optional_capability(value, where=where)
    if level is None:
        raise ConfigError(f"policy {where}: missing capability level")
    return level


def _optional_capability(value: object, *, where: str) -> Capability | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return Capability(value.strip().upper())
        except ValueError as exc:
            raise ConfigError(f"policy {where}: {value!r} is not a capability level") from exc
    raise ConfigError(f"policy {where}: {value!r} is not a capability level")


def _decision(value: object, *, where: str) -> Decision:
    if isinstance(value, str):
        decision = _DECISIONS.get(value.strip().casefold())
        if decision is not None:
            return decision
    raise ConfigError(f"policy {where}: {value!r} is not one of allow, confirm, deny")


def _optional_str(value: object, *, where: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise ConfigError(f"policy {where}: expected a string")


def _string_tuple(value: object, *, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        items: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ConfigError(f"policy {where}: expected a list of strings")
            items.append(item)
        return tuple(items)
    raise ConfigError(f"policy {where}: expected a string or list of strings")


def _non_negative_int(value: object, default: int, *, where: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(f"policy {where}: expected a non-negative integer")
    return value


def _optional_date(value: object, *, where: str) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value.strip())
        except ValueError as exc:
            raise ConfigError(f"policy {where}: {value!r} is not an ISO date") from exc
    raise ConfigError(f"policy {where}: expected an ISO date")


class PolicyStore:
    """Hot-reloading holder. Re-reads when the file's mtime or size changes."""

    def __init__(
        self,
        path: Path = DEFAULT_POLICY_PATH,
        *,
        variables: Mapping[str, str] | None = None,
    ) -> None:
        self._path = path
        self._variables = dict(variables or {})
        self._stamp: tuple[float, int] | None = None
        self._policy: Policy | None = None

    @property
    def path(self) -> Path:
        return self._path

    def reload(self) -> Policy:
        """Force a re-read. ``get`` already notices a changed file; an operator
        who has just edited the policy should not have to trust that."""
        self._stamp = None
        self._policy = None
        return self.get()

    def get(self) -> Policy:
        try:
            stat = self._path.stat()
            stamp = (stat.st_mtime, stat.st_size)
        except OSError as exc:
            log.critical("vyomel.security.policy_missing", path=str(self._path), error=str(exc))
            return DENY_ALL
        if self._policy is None or stamp != self._stamp:
            self._policy = safe_load_policy(self._path, variables=self._variables)
            self._stamp = stamp
            log.info(
                "vyomel.security.policy_loaded",
                path=str(self._path),
                version=self._policy.version,
                policy_hash=self._policy.hash,
                rules=len(self._policy.rules),
            )
        return self._policy


@functools.lru_cache(maxsize=8)
def _shared_store(policy_path: Path, scratch_dir: Path, workspace_root: Path) -> PolicyStore:
    return PolicyStore(policy_path, variables=variables_for(scratch_dir, workspace_root))


def store_for(settings: Settings) -> PolicyStore:
    """One hot-reloading store per (path, sandbox) triple, shared process-wide.

    The planner and the dispatch gate must agree on the policy in force; two
    independently loaded copies could disagree for as long as a plan takes to
    install, which is exactly the window an attacker would want.
    """
    return _shared_store(settings.policy_path, settings.scratch_dir, settings.workspace_root)


def variables_for(scratch_dir: Path, workspace_root: Path) -> dict[str, str]:
    """Substitutions available inside policy patterns."""
    return {
        "scratch_dir": str(scratch_dir).replace("\\", "/"),
        "workspace_root": str(workspace_root).replace("\\", "/"),
        "home": str(Path.home()).replace("\\", "/"),
    }
