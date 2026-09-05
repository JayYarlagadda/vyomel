"""Prometheus metrics (docs/10 §3)."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

REGISTRY = CollectorRegistry(auto_describe=True)

# --- task ---
TASKS_TOTAL = Counter(
    "vyomel_tasks_total",
    "Tasks reaching a recorded status.",
    ["status", "origin"],
    registry=REGISTRY,
)
TASK_DURATION = Histogram(
    "vyomel_task_duration_seconds",
    "Task wall-clock duration.",
    ["status"],
    registry=REGISTRY,
)
TASK_STEPS = Histogram("vyomel_task_steps", "Steps per finished task.", registry=REGISTRY)
TASK_REPLANS = Counter("vyomel_task_replans_total", "Replans issued.", registry=REGISTRY)
TASK_COST = Histogram("vyomel_task_cost_usd", "Recorded cost per task.", registry=REGISTRY)
HUMAN_INTERVENTIONS = Counter(
    "vyomel_human_interventions_total",
    "Times a task stopped for a human.",
    ["reason"],
    registry=REGISTRY,
)

# --- action / tool ---
ACTIONS_TOTAL = Counter(
    "vyomel_actions_total",
    "Actions by tool, status, and capability.",
    ["tool", "status", "capability"],
    registry=REGISTRY,
)
ACTION_DURATION = Histogram(
    "vyomel_action_duration_seconds",
    "Action execution duration.",
    ["tool"],
    registry=REGISTRY,
)
ACTION_RETRIES = Counter(
    "vyomel_action_retries_total",
    "Retryable action failures.",
    ["tool", "error_code"],
    registry=REGISTRY,
)
ACTUATION_TIER = Counter(
    "vyomel_actuation_tier_total",
    "Actuation tier used to resolve a target.",
    ["tier"],
    registry=REGISTRY,
)
TOOL_ERRORS = Counter(
    "vyomel_tool_errors_total",
    "Structured tool errors.",
    ["tool", "code", "retryable"],
    registry=REGISTRY,
)
DEAD_LETTERS = Counter(
    "vyomel_dead_letters_total",
    "Actions moved to the dead-letter table.",
    ["tool"],
    registry=REGISTRY,
)

# --- security ---
POLICY_DECISIONS = Counter(
    "vyomel_policy_decisions_total",
    "Policy evaluations.",
    ["decision", "capability", "rule_id"],
    registry=REGISTRY,
)
APPROVALS = Counter(
    "vyomel_approvals_total",
    "Approval outcomes.",
    ["outcome", "capability"],
    registry=REGISTRY,
)
APPROVAL_WAIT = Histogram(
    "vyomel_approval_wait_seconds",
    "Time from approval request to decision.",
    registry=REGISTRY,
)
PRIVACY_ROUTING_BLOCKS = Counter(
    "vyomel_privacy_routing_blocks_total",
    "Cloud-routing refusals for sensitive data.",
    registry=REGISTRY,
)
REDACTIONS = Counter(
    "vyomel_redactions_total",
    "Values scrubbed before a sink.",
    ["sink"],
    registry=REGISTRY,
)

# --- verification ---
VERIFICATIONS = Counter(
    "vyomel_verifications_total",
    "Verification checks.",
    ["type", "outcome"],
    registry=REGISTRY,
)
UNVERIFIED_ACTIONS = Counter(
    "vyomel_unverified_actions_total",
    "Actions that finished UNVERIFIED.",
    ["tool"],
    registry=REGISTRY,
)
VERIFICATION_DURATION = Histogram(
    "vyomel_verification_duration_seconds",
    "Verification latency.",
    ["type"],
    registry=REGISTRY,
)

# --- model ---
MODEL_CALLS = Counter(
    "vyomel_model_calls_total",
    "Model completions.",
    ["provider", "model", "purpose", "cache_hit"],
    registry=REGISTRY,
)
MODEL_TOKENS = Counter(
    "vyomel_model_tokens_total",
    "Tokens billed.",
    ["provider", "model", "direction"],
    registry=REGISTRY,
)
MODEL_TTFT = Histogram(
    "vyomel_model_ttft_seconds",
    "Time to first token.",
    ["provider", "model"],
    registry=REGISTRY,
)
MODEL_LATENCY = Histogram(
    "vyomel_model_latency_seconds",
    "End-to-end model latency.",
    ["provider", "model"],
    registry=REGISTRY,
)
MODEL_COST = Counter(
    "vyomel_model_cost_usd_total",
    "Model spend.",
    ["provider", "model"],
    registry=REGISTRY,
)
MODEL_FAILOVERS = Counter(
    "vyomel_model_failovers_total",
    "Provider failovers.",
    ["from_provider", "to", "reason"],
    registry=REGISTRY,
)
CIRCUIT_BREAKER = Gauge(
    "vyomel_circuit_breaker_state",
    "0 closed, 1 open, 2 half-open.",
    ["provider"],
    registry=REGISTRY,
)

# --- memory ---
RETRIEVALS = Counter(
    "vyomel_retrievals_total",
    "Retrieval calls.",
    ["strategy"],
    registry=REGISTRY,
)
RETRIEVAL_LATENCY = Histogram(
    "vyomel_retrieval_latency_seconds",
    "Retrieval latency.",
    ["strategy"],
    registry=REGISTRY,
)
INGESTION_DOCUMENTS = Counter(
    "vyomel_ingestion_documents_total",
    "Ingested documents.",
    ["status"],
    registry=REGISTRY,
)
INGESTION_CHUNKS = Counter(
    "vyomel_ingestion_chunks_total",
    "Chunks written during ingest.",
    registry=REGISTRY,
)
GRAPH_ENTITIES = Gauge(
    "vyomel_context_graph_entities",
    "Entities in the context graph.",
    ["type"],
    registry=REGISTRY,
)

# --- runtime ---
QUEUE_DEPTH = Gauge(
    "vyomel_queue_depth",
    "Redis stream length.",
    ["stream"],
    registry=REGISTRY,
)
WORKERS_ACTIVE = Gauge(
    "vyomel_workers_active",
    "Worker processes in run_forever.",
    registry=REGISTRY,
)
LEASES_RECLAIMED = Counter(
    "vyomel_leases_reclaimed_total",
    "Actions returned to READY by the reaper.",
    registry=REGISTRY,
)
QUEUE_WAIT = Histogram(
    "vyomel_action_queue_wait_seconds",
    "Time from DISPATCHED to RUNNING.",
    registry=REGISTRY,
)


def exposition() -> bytes:
    return generate_latest(REGISTRY)
