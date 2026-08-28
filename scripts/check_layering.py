"""Enforce the layering rules in docs/02-ARCHITECTURE.md section 3.

Dependencies point downward only. An upward or cyclic import is a build
failure, not a code-review comment -- architecture that is only enforced by
review erodes within weeks.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "astra"

# Each layer may import only from the layers listed here (plus astra.core,
# which is universally available, and itself).
ALLOWED: dict[str, set[str]] = {
    "core": set(),
    "obs": set(),
    "store": set(),
    "models": {"store", "obs"},
    "perception": {"obs"},
    "security": {"store", "obs"},
    "memory": {"models", "store", "obs"},
    "tools": {"perception", "models", "obs"},
    "verify": {"perception", "tools", "models", "obs"},
    "runtime": {"tools", "verify", "security", "store", "models", "obs"},
    "planner": {"models", "memory", "tools", "obs"},
    "orchestrator": {"planner", "security", "memory", "runtime", "store", "models", "obs"},
    "api": {"orchestrator", "store", "obs", "security"},
    "cli": {"api", "orchestrator", "store", "obs"},
    "prompts": set(),
}

UNIVERSAL = {"core"}


def layer_of(path: Path) -> str | None:
    relative = path.relative_to(PACKAGE)
    return relative.parts[0] if len(relative.parts) > 1 else None


def imported_layers(tree: ast.AST) -> set[str]:
    layers: set[str] = set()
    for node in ast.walk(tree):
        module: str | None = None
        if isinstance(node, ast.ImportFrom):
            module = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("astra."):
                    layers.add(alias.name.split(".")[1])
            continue
        if module and module.startswith("astra."):
            parts = module.split(".")
            if len(parts) > 1:
                layers.add(parts[1])
    return layers


def main() -> int:
    violations: list[str] = []

    for file in sorted(PACKAGE.rglob("*.py")):
        if "migrations" in file.parts:
            continue
        layer = layer_of(file)
        if layer is None or layer not in ALLOWED:
            continue
        allowed = ALLOWED[layer] | UNIVERSAL | {layer}
        try:
            tree = ast.parse(file.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            violations.append(f"{file}: syntax error: {exc}")
            continue
        for imported in imported_layers(tree) - allowed:
            violations.append(
                f"{file.relative_to(ROOT)}: '{layer}' may not import '{imported}' "
                f"(allowed: {sorted(allowed)})"
            )

    if violations:
        print("Layering violations:\n")
        for violation in violations:
            print(f"  {violation}")
        return 1

    print("Layering OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
