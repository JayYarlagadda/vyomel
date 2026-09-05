"""Normalize gated metrics from heterogeneous suite result JSON.

Suites evolved different shapes (``results.json`` vs ``summary.json``, nested
``configurations`` vs flat rates). Compare needs one flat map of metric name →
float so thresholds in ``docs/11-EVALUATION.md`` §10 apply uniformly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Metrics that regression gating watches (docs/11 §10).
GATED_METRICS = frozenset(
    {
        "task_completion_rate",
        "tool_call_accuracy",
        "recall_at_10",
        "injection_success_rate",
        "verification_catch_rate",
        "cost_per_task",
    }
)


def load_results(path: Path) -> dict[str, Any]:
    """Load a results file or a directory containing results/summary JSON."""
    if path.is_dir():
        for name in ("results.json", "summary.json", "gated.json"):
            candidate = path / name
            if candidate.is_file():
                path = candidate
                break
        else:
            raise FileNotFoundError(f"no results.json/summary.json under {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"results must be a JSON object: {path}")
    return data


def extract_metrics(payload: dict[str, Any]) -> dict[str, float]:
    """Pull gated (and related) metrics into a flat float map."""
    out: dict[str, float] = {}

    def _put(name: str, value: object) -> None:
        if isinstance(value, bool):
            return
        if isinstance(value, int | float):
            out[name] = float(value)

    # Flat top-level (agent, browser, desktop, security, serving summaries).
    for key in (
        "task_completion_rate",
        "tool_call_accuracy",
        "success_rate",
        "injection_success_rate",
        "verification_catch_rate",
        "cost_per_task",
        "human_intervention_rate",
        "vision_tier_ratio",
        "throughput_speedup_at_c16",
    ):
        if key in payload:
            _put(key, payload[key])

    # Explicit gated block (baselines / compare candidates).
    gated = payload.get("gated")
    if isinstance(gated, dict):
        for key, value in gated.items():
            _put(str(key), value)

    # RAG: metrics.recall_at_10.hybrid (preferred) or bare float.
    metrics = payload.get("metrics")
    if isinstance(metrics, dict):
        recall = metrics.get("recall_at_10")
        if isinstance(recall, dict):
            hybrid = recall.get("hybrid")
            if hybrid is not None:
                _put("recall_at_10", hybrid)
            for strategy, value in recall.items():
                _put(f"recall_at_10_{strategy}", value)
        elif isinstance(recall, int | float):
            _put("recall_at_10", recall)

    # Agent: configurations.<name>.{task_completion_rate, tool_call_accuracy}
    configs = payload.get("configurations")
    if isinstance(configs, dict):
        completion: list[float] = []
        accuracy: list[float] = []
        for name, cfg in configs.items():
            if not isinstance(cfg, dict):
                continue
            if "task_completion_rate" in cfg:
                rate = float(cfg["task_completion_rate"])
                completion.append(rate)
                _put(f"task_completion_rate_{name}", rate)
            if "tool_call_accuracy" in cfg:
                rate = float(cfg["tool_call_accuracy"])
                accuracy.append(rate)
                _put(f"tool_call_accuracy_{name}", rate)
            if "ok" in cfg and cfg.get("lost_fetches") is not None:
                # Longrun: treat any lost/dupe as failure signal via cost-like proxy.
                lost = float(cfg.get("lost_fetches") or 0)
                dupes = float(cfg.get("duplicate_fetches") or 0)
                _put(f"longrun_lost_{name}", lost)
                _put(f"longrun_dupes_{name}", dupes)
        if completion and "task_completion_rate" not in out:
            _put("task_completion_rate", min(completion))
        if accuracy and "tool_call_accuracy" not in out:
            _put("tool_call_accuracy", min(accuracy))

    # Ablation tables may nest under "ablations".
    ablations = payload.get("ablations")
    if isinstance(ablations, dict):
        rag = ablations.get("rag")
        if isinstance(rag, dict) and "recall_at_10" in rag and "recall_at_10" not in out:
            _put("recall_at_10", rag["recall_at_10"])
        planner = ablations.get("planner")
        if isinstance(planner, dict):
            if "task_completion_rate" in planner and "task_completion_rate" not in out:
                _put("task_completion_rate", planner["task_completion_rate"])
            if "tool_call_accuracy" in planner and "tool_call_accuracy" not in out:
                _put("tool_call_accuracy", planner["tool_call_accuracy"])

    return out
