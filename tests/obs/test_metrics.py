"""Prometheus series from docs/10 §3 (FR-802)."""

from __future__ import annotations

import pytest

from vyomel.obs.metrics import (
    ACTIONS_TOTAL,
    HUMAN_INTERVENTIONS,
    LEASES_RECLAIMED,
    POLICY_DECISIONS,
    QUEUE_DEPTH,
    TASKS_TOTAL,
    UNVERIFIED_ACTIONS,
    exposition,
)

REQUIRED = (
    "vyomel_tasks_total",
    "vyomel_task_duration_seconds",
    "vyomel_task_steps",
    "vyomel_task_replans_total",
    "vyomel_task_cost_usd",
    "vyomel_human_interventions_total",
    "vyomel_actions_total",
    "vyomel_action_duration_seconds",
    "vyomel_action_retries_total",
    "vyomel_actuation_tier_total",
    "vyomel_tool_errors_total",
    "vyomel_dead_letters_total",
    "vyomel_policy_decisions_total",
    "vyomel_approvals_total",
    "vyomel_approval_wait_seconds",
    "vyomel_privacy_routing_blocks_total",
    "vyomel_redactions_total",
    "vyomel_verifications_total",
    "vyomel_unverified_actions_total",
    "vyomel_verification_duration_seconds",
    "vyomel_model_calls_total",
    "vyomel_model_tokens_total",
    "vyomel_model_ttft_seconds",
    "vyomel_model_latency_seconds",
    "vyomel_model_cost_usd_total",
    "vyomel_model_failovers_total",
    "vyomel_circuit_breaker_state",
    "vyomel_retrievals_total",
    "vyomel_retrieval_latency_seconds",
    "vyomel_ingestion_documents_total",
    "vyomel_ingestion_chunks_total",
    "vyomel_context_graph_entities",
    "vyomel_queue_depth",
    "vyomel_workers_active",
    "vyomel_leases_reclaimed_total",
    "vyomel_action_queue_wait_seconds",
)


@pytest.mark.req("FR-802")
def test_exposition_declares_every_series_in_the_spec() -> None:
    TASKS_TOTAL.labels(status="CREATED", origin="api").inc()
    ACTIONS_TOTAL.labels(tool="fs.list_dir", status="SUCCEEDED", capability="L0").inc()
    POLICY_DECISIONS.labels(decision="ALLOW", capability="L0", rule_id="default-allow").inc()
    UNVERIFIED_ACTIONS.labels(tool="desktop.set_field").inc()
    HUMAN_INTERVENTIONS.labels(reason="approval").inc()
    LEASES_RECLAIMED.inc()
    QUEUE_DEPTH.labels(stream="vyomel:actions").set(3)
    body = exposition().decode()
    missing = [name for name in REQUIRED if name not in body]
    assert missing == []
