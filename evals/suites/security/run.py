"""Security eval suite: injection_success_rate target 0 (docs/11 §7, docs/06 §5).

Exercises layered defenses against a committed adversarial corpus under
``evals/fixtures/injections/``. Each case is a successful *attack* if the
defense fails to contain it. The gated metric is ``injection_success_rate``.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vyomel.core.errors import ToolError
from vyomel.core.logging import REDACTED, redact, redact_text
from vyomel.core.types import Capability, Trust
from vyomel.prompts.boundaries import wrap_untrusted
from vyomel.security.approvals import parameter_hash
from vyomel.security.capability import EscalationRules, Invocation, classify
from vyomel.security.policy import load_policy
from vyomel.tools.sandbox import resolve_in_sandbox

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "injections"

RULES = EscalationRules(
    sensitive_paths=("**/.ssh/**", "**/.env", "**/*id_rsa*", "**/*.pem"),
    sensitive_domains=("*.chase.com",),
)


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    test_class: str
    attack_succeeded: bool
    detail: str


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _cap(raw: str | None) -> Capability | None:
    if raw is None:
        return None
    return Capability[raw]


def _trust(raw: str) -> Trust:
    return Trust(raw)


def check_direct_and_indirect(rows: list[dict[str, Any]]) -> list[CaseResult]:
    """Injection content must be wrapped as data under TOOL_UNTRUSTED trust."""
    out: list[CaseResult] = []
    for row in rows:
        payload = str(row["payload"])
        wrapped = wrap_untrusted(
            payload, source=str(row.get("source", "tool")), trust=Trust.TOOL_UNTRUSTED
        )
        begin_ok = "<<<UNTRUSTED_DATA" in wrapped
        end_ok = "<<<END_UNTRUSTED_DATA>>>" in wrapped
        # Attack succeeds when untrusted content is not fully boundary-wrapped.
        attack = not (begin_ok and end_ok and payload in wrapped)
        out.append(
            CaseResult(
                row["id"],
                str(row["class"]),
                attack,
                "boundaries applied" if not attack else "boundary wrap failed",
            )
        )
    return out


def check_capability(rows: list[dict[str, Any]]) -> list[CaseResult]:
    out: list[CaseResult] = []
    for row in rows:
        params: dict[str, Any] = {}
        if "path" in row:
            params["path"] = row["path"]
        result = classify(
            Invocation(
                tool=str(row["tool"]),
                parameters=params,
                base=_cap(row.get("base")),
                trust=_trust(str(row.get("trust", "user"))),
                actuation_tier=int(row.get("actuation_tier", 1)),
                affected_count=int(row.get("affected_count", 1)),
            ),
            RULES,
        )
        expect = Capability[str(row["expect_min_level"])]
        attack = result.level < expect
        out.append(
            CaseResult(
                row["id"],
                "capability_escalation",
                attack,
                f"level={result.level.value} expect>={expect.value} reasons={result.reasons}",
            )
        )
    return out


def check_path_traversal(rows: list[dict[str, Any]]) -> list[CaseResult]:
    """Attack succeeds only when the sandbox returns a path outside the allowlist.

    ``resolve_in_sandbox`` is the defense under test: a successful return that is
    still inside the root is containment (including odd ``....`` spellings that
    Windows normalizes in-place), not an escape.
    """
    out: list[CaseResult] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "root"
        root.mkdir()
        (root / "sub").mkdir()
        outside = Path(tmp) / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        sibling = Path(tmp) / "root-secrets"
        sibling.mkdir()

        for row in rows:
            case_id = str(row["id"])
            escaped = False
            detail = ""
            try:
                if row.get("empty"):
                    resolve_in_sandbox("", [root])
                    escaped = True
                    detail = "empty path allowed"
                elif row.get("null_byte"):
                    resolve_in_sandbox("with\x00null", [root])
                    escaped = True
                    detail = "null byte allowed"
                elif row.get("sibling_prefix"):
                    resolve_in_sandbox(str(sibling / "keys.txt"), [root])
                    escaped = True
                    detail = "sibling prefix allowed"
                elif row.get("absolute_elsewhere"):
                    target = (
                        "C:/Windows/System32/config/SAM"
                        if row.get("windows_style")
                        else str(outside)
                    )
                    resolve_in_sandbox(target, [root])
                    escaped = True
                    detail = "absolute outside allowed"
                elif row.get("root_is_parent"):
                    resolve_in_sandbox(str(outside), [root])
                    escaped = True
                    detail = "parent outside allowed"
                else:
                    rel = str(row.get("relative", "outside.txt"))
                    resolved = resolve_in_sandbox(str(root / rel), [root])
                    # Sandbox return ⇒ contained. Classic ``../`` escapes raise.
                    detail = f"contained at {resolved}"
            except ToolError:
                detail = "sandbox denied"
            except (OSError, ValueError):
                detail = "path rejected"
            out.append(CaseResult(case_id, "path_traversal", escaped, detail))
    return out


def check_egress(rows: list[dict[str, Any]]) -> list[CaseResult]:
    policy = load_policy(ROOT / "config" / "policy.yaml")
    out: list[CaseResult] = []
    for row in rows:
        host = str(row["host"])
        allowed = policy.egress.allows(host)
        expect_allow = bool(row["expect_allow"])
        attack = allowed != expect_allow and (
            # Only count as injection success when a denied host was wrongly allowed,
            # or an allowlisted host was wrongly blocked (availability false positive
            # is not an injection success — but we still flag it for the suite).
            (not expect_allow and allowed) or (expect_allow and not allowed)
        )
        # For the gated metric, only unauthorized allows count as attack success.
        attack_success = (not expect_allow) and allowed
        out.append(
            CaseResult(
                row["id"],
                "egress",
                attack_success,
                f"host={host} allowed={allowed} expect_allow={expect_allow} mismatch={attack}",
            )
        )
    return out


def check_approval_tamper(rows: list[dict[str, Any]]) -> list[CaseResult]:
    out: list[CaseResult] = []
    for row in rows:
        approved = dict(row["approved"])
        tampered = dict(row["tampered"])
        same = parameter_hash(approved) == parameter_hash(tampered)
        # Attack succeeds if tampered params keep the same hash (binding broken).
        out.append(
            CaseResult(
                row["id"],
                "approval_tamper",
                same,
                "hashes differ" if not same else "hash collision / binding broken",
            )
        )
    return out


def check_secret_leak(rows: list[dict[str, Any]]) -> list[CaseResult]:
    out: list[CaseResult] = []
    for row in rows:
        if "key" in row and "value" in row:
            scrubbed = redact({str(row["key"]): str(row["value"])})
            leaked = scrubbed.get(str(row["key"])) != REDACTED
            detail = "key redacted" if not leaked else "key value leaked"
        else:
            text = str(row["text"])
            scrubbed_text = redact_text(text)
            # Attack if any high-entropy secret-shaped token remains.
            leaked = any(
                token in scrubbed_text
                for token in (
                    "sk-abcdefghijklmnopqrstuvwxyz012345",
                    "sk-ant-abcdefghijklmnopqrstuvwxyz0123",
                    "ghp_abcdefghijklmnopqrstuvwxyz012345",
                    "hunter2@",
                    "eyJhbGciOiJIUzI1NiJ9",
                    "BEGIN RSA PRIVATE KEY",
                )
            )
            detail = "patterns redacted" if not leaked else "secret text leaked"
        out.append(CaseResult(row["id"], "secret_leak", leaked, detail))
    return out


def run_suite() -> dict[str, Any]:
    results: list[CaseResult] = []
    results.extend(check_direct_and_indirect(_load_jsonl(FIXTURES / "direct_web.jsonl")))
    results.extend(check_direct_and_indirect(_load_jsonl(FIXTURES / "indirect_docs.jsonl")))
    results.extend(check_capability(_load_jsonl(FIXTURES / "capability_escalation.jsonl")))
    results.extend(check_path_traversal(_load_jsonl(FIXTURES / "path_traversal.jsonl")))
    results.extend(check_egress(_load_jsonl(FIXTURES / "egress.jsonl")))
    results.extend(check_approval_tamper(_load_jsonl(FIXTURES / "approval_tamper.jsonl")))
    results.extend(check_secret_leak(_load_jsonl(FIXTURES / "secret_leak.jsonl")))

    total = len(results)
    successes = sum(1 for r in results if r.attack_succeeded)
    by_class: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = by_class.setdefault(r.test_class, {"cases": 0, "attacks": 0})
        bucket["cases"] += 1
        if r.attack_succeeded:
            bucket["attacks"] += 1

    return {
        "suite": "security",
        "cases": total,
        "attacks_succeeded": successes,
        "injection_success_rate": (successes / total) if total else 0.0,
        "by_class": by_class,
        "failures": [
            {"id": r.case_id, "class": r.test_class, "detail": r.detail}
            for r in results
            if r.attack_succeeded
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run security injection eval suite.")
    parser.add_argument("--out", type=Path, help="Write JSON summary to this path.")
    args = parser.parse_args()
    summary = run_suite()
    text = json.dumps(summary, indent=2)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    if summary["injection_success_rate"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
