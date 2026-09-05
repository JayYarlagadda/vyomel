"""Ablation tables for RAG, planner models, and routing (M12 / docs/11).

Aggregates committed milestone numbers and runs cheap in-process checks that do
not need Postgres. Full RAG re-ingest remains ``evals/suites/rag/recall.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vyomel.core.config import Settings
from vyomel.core.errors import PrivacyRoutingViolation
from vyomel.core.types import Sensitivity
from vyomel.models.catalog import load_model_config, preferred_backends
from vyomel.models.router import get_planner_provider, reset_breakers


def rag_ablation() -> dict[str, Any]:
    m4 = json.loads((ROOT / "evals/results/2026-09-02-m4/results.json").read_text(encoding="utf-8"))
    recall = m4["metrics"]["recall_at_10"]
    return {
        "source": "evals/results/2026-09-02-m4/results.json",
        "embedder": m4.get("embedder"),
        "k": m4.get("k", 10),
        "strategies": {
            "hybrid": recall["hybrid"],
            "lexical": recall["lexical"],
            "vector": recall["vector"],
        },
        "recall_at_10": recall["hybrid"],
        "notes": (
            "Strategy ablation on hashing-384 embedder. Hybrid wins; vector-only is weak "
            "without a real embedding model (see docs/08)."
        ),
    }


def planner_ablation() -> dict[str, Any]:
    m5 = json.loads((ROOT / "evals/results/2026-09-02-m5/results.json").read_text(encoding="utf-8"))
    configs = m5["configurations"]
    rates = [float(c["task_completion_rate"]) for c in configs.values()]
    tools = [float(c["tool_call_accuracy"]) for c in configs.values()]
    return {
        "source": "evals/results/2026-09-02-m5/results.json",
        "models": {
            name: {
                "backend": cfg["backend"],
                "task_completion_rate": cfg["task_completion_rate"],
                "tool_call_accuracy": cfg["tool_call_accuracy"],
            }
            for name, cfg in configs.items()
        },
        "task_completion_rate": min(rates),
        "tool_call_accuracy": min(tools),
        "notes": "Mock planner variants; both configs at 1.0 on the 100-task fixture.",
    }


def routing_ablation() -> dict[str, Any]:
    """Purpose-table and privacy/offline routing checks (no network)."""
    reset_breakers()
    cfg = load_model_config(str(ROOT / "config" / "models.yaml"))
    purposes = {
        purpose: preferred_backends(purpose, config=cfg)
        for purpose in (
            "planner.decompose",
            "extract",
            "classify",
            "summarize",
            "embed",
        )
    }
    local_first = {
        p: prefer[0] == "local"
        for p, prefer in purposes.items()
        if p in {"extract", "classify", "summarize", "embed"}
    }

    privacy_blocks_remote = False
    try:
        get_planner_provider(
            Settings(env="dev", planner_backend="openai", openai_api_key="sk-test"),
            sensitivity=Sensitivity.SENSITIVE,
        )
    except PrivacyRoutingViolation:
        privacy_blocks_remote = True

    offline_blocks_remote = False
    try:
        get_planner_provider(
            Settings(env="dev", planner_backend="openai", openai_api_key="sk-test", offline=True),
            sensitivity=Sensitivity.PUBLIC,
        )
    except PrivacyRoutingViolation:
        offline_blocks_remote = True

    checks = {
        "extract_classify_summarize_prefer_local": all(local_first.values()),
        "sensitive_blocks_remote": privacy_blocks_remote,
        "offline_blocks_remote": offline_blocks_remote,
    }
    return {
        "source": "config/models.yaml + router checks",
        "purposes": purposes,
        "checks": checks,
        "routing_pass_rate": sum(1 for v in checks.values() if v) / len(checks),
        "notes": (
            "Local-first for extract/classify/summarize/embed; privacy and offline "
            "fail closed on remote backends."
        ),
    }


def run() -> dict[str, Any]:
    rag = rag_ablation()
    planner = planner_ablation()
    routing = routing_ablation()
    return {
        "suite": "ablations",
        "ablations": {
            "rag": rag,
            "planner": planner,
            "routing": routing,
        },
        "gated": {
            "recall_at_10": rag["recall_at_10"],
            "task_completion_rate": planner["task_completion_rate"],
            "tool_call_accuracy": planner["tool_call_accuracy"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build RAG/planner/routing ablation tables.")
    parser.add_argument("--out", type=Path, help="Write JSON to this path.")
    args = parser.parse_args()
    summary = run()
    text = json.dumps(summary, indent=2)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    routing = summary["ablations"]["routing"]["checks"]
    if not all(routing.values()):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
