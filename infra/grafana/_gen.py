"""Write versioned Grafana dashboards (docs/10 §5). Run from repo root."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "dashboards"

DATASOURCE = {"type": "prometheus", "uid": "prometheus"}


def _panel(pid: int, title: str, expr: str, *, x: int, y: int, legend: str = "") -> dict:
    return {
        "id": pid,
        "type": "timeseries",
        "title": title,
        "gridPos": {"h": 8, "w": 12, "x": x, "y": y},
        "datasource": DATASOURCE,
        "fieldConfig": {
            "defaults": {"custom": {"drawStyle": "line", "lineWidth": 1}},
            "overrides": [],
        },
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom"},
            "tooltip": {"mode": "single"},
        },
        "targets": [
            {
                "datasource": DATASOURCE,
                "expr": expr,
                "legendFormat": legend or title,
                "refId": "A",
            }
        ],
    }


def _dashboard(uid: str, title: str, panels: list[dict]) -> dict:
    return {
        "annotations": {"list": []},
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 0,
        "id": None,
        "links": [],
        "liveNow": False,
        "panels": panels,
        "refresh": "30s",
        "schemaVersion": 38,
        "style": "dark",
        "tags": ["vyomel", uid],
        "templating": {"list": []},
        "time": {"from": "now-6h", "to": "now"},
        "timepicker": {},
        "timezone": "utc",
        "title": title,
        "uid": uid,
        "version": 1,
        "weekStart": "",
    }


DASHBOARDS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "vyomel-task-health",
        "Vyomel Task Health",
        [
            ("Tasks created", "sum(rate(vyomel_tasks_total[5m]))"),
            (
                "Task duration p95",
                "histogram_quantile(0.95, sum(rate(vyomel_task_duration_seconds_bucket[5m])) by (le, status))",
            ),
            ("Replans", "sum(rate(vyomel_task_replans_total[5m]))"),
            ("Human interventions", "sum(rate(vyomel_human_interventions_total[5m])) by (reason)"),
            (
                "Cost per task",
                "histogram_quantile(0.50, sum(rate(vyomel_task_cost_usd_bucket[5m])) by (le))",
            ),
            (
                "Steps per task",
                "histogram_quantile(0.50, sum(rate(vyomel_task_steps_bucket[5m])) by (le))",
            ),
        ],
    ),
    (
        "vyomel-tool-reliability",
        "Vyomel Tool Reliability",
        [
            ("Actions", "sum(rate(vyomel_actions_total[5m])) by (tool, status)"),
            (
                "Action duration p95",
                "histogram_quantile(0.95, sum(rate(vyomel_action_duration_seconds_bucket[5m])) by (le, tool))",
            ),
            ("Tool errors", "sum(rate(vyomel_tool_errors_total[5m])) by (tool, code)"),
            ("Actuation tier", "sum(rate(vyomel_actuation_tier_total[5m])) by (tier)"),
            ("Dead letters", "sum(rate(vyomel_dead_letters_total[5m])) by (tool)"),
            ("Unverified actions", "sum(rate(vyomel_unverified_actions_total[5m])) by (tool)"),
        ],
    ),
    (
        "vyomel-model-performance",
        "Vyomel Model Performance",
        [
            (
                "Model calls",
                "sum(rate(vyomel_model_calls_total[5m])) by (provider, model, purpose)",
            ),
            (
                "TTFT p95",
                "histogram_quantile(0.95, sum(rate(vyomel_model_ttft_seconds_bucket[5m])) by (le, provider, model))",
            ),
            (
                "Latency p95",
                "histogram_quantile(0.95, sum(rate(vyomel_model_latency_seconds_bucket[5m])) by (le, provider, model))",
            ),
            ("Tokens", "sum(rate(vyomel_model_tokens_total[5m])) by (provider, direction)"),
            ("Failovers", "sum(rate(vyomel_model_failovers_total[5m])) by (from, to, reason)"),
            ("Circuit breaker", "vyomel_circuit_breaker_state"),
        ],
    ),
    (
        "vyomel-security",
        "Vyomel Security",
        [
            ("Policy decisions", "sum(rate(vyomel_policy_decisions_total[5m])) by (decision)"),
            ("Approvals", "sum(rate(vyomel_approvals_total[5m])) by (outcome, capability)"),
            (
                "Approval wait p95",
                "histogram_quantile(0.95, sum(rate(vyomel_approval_wait_seconds_bucket[5m])) by (le))",
            ),
            ("Privacy routing blocks", "sum(rate(vyomel_privacy_routing_blocks_total[5m]))"),
            ("Redactions", "sum(rate(vyomel_redactions_total[5m])) by (sink)"),
            (
                "L4 denials",
                'sum(rate(vyomel_policy_decisions_total{capability="L4",decision="DENY"}[5m]))',
            ),
        ],
    ),
    (
        "vyomel-memory",
        "Vyomel Memory",
        [
            ("Retrievals", "sum(rate(vyomel_retrievals_total[5m])) by (strategy)"),
            (
                "Retrieval latency p95",
                "histogram_quantile(0.95, sum(rate(vyomel_retrieval_latency_seconds_bucket[5m])) by (le, strategy))",
            ),
            ("Ingestion documents", "sum(rate(vyomel_ingestion_documents_total[5m])) by (status)"),
            ("Ingestion chunks", "sum(rate(vyomel_ingestion_chunks_total[5m]))"),
            ("Graph entities", "sum(vyomel_context_graph_entities) by (type)"),
        ],
    ),
    (
        "vyomel-runtime",
        "Vyomel Runtime",
        [
            ("Queue depth", "vyomel_queue_depth"),
            ("Workers active", "vyomel_workers_active"),
            ("Leases reclaimed", "sum(rate(vyomel_leases_reclaimed_total[5m]))"),
            (
                "Queue wait p95",
                "histogram_quantile(0.95, sum(rate(vyomel_action_queue_wait_seconds_bucket[5m])) by (le))",
            ),
            ("Verifications", "sum(rate(vyomel_verifications_total[5m])) by (type, outcome)"),
        ],
    ),
]


def main() -> None:
    for uid, title, specs in DASHBOARDS:
        panels = []
        for index, (panel_title, expr) in enumerate(specs):
            panels.append(
                _panel(index + 1, panel_title, expr, x=(index % 2) * 12, y=(index // 2) * 8)
            )
        path = ROOT / f"{uid}.json"
        path.write_text(
            json.dumps(_dashboard(uid, title, panels), indent=2) + "\n", encoding="utf-8"
        )
        print(path.name)


if __name__ == "__main__":
    main()
