# 10 — Observability

Status: **Approved baseline (v1.0)**

Agent systems fail in ways that are invisible without instrumentation: the plan was subtly wrong, retrieval missed the key document, the model chose a plausible-but-wrong tool, a selector silently matched the wrong element. "It didn't work" is not a debuggable statement. Traces and metrics turn it into one.

This layer also directly extends the author's OpenTelemetry work at Microsoft, which makes it a natural depth area in interviews.

---

## 1. Span hierarchy

```
task  (root span, trace_id propagated everywhere)
├── plan
│   ├── memory.retrieve
│   │   ├── retrieve.vector
│   │   ├── retrieve.lexical
│   │   └── retrieve.fuse
│   └── model.complete (purpose=plan)
├── step[1]
│   └── action[1]
│       ├── policy.evaluate
│       ├── approval.wait          (only when gated)
│       ├── tool.execute
│       │   └── actuation (tier=2)
│       └── verify
│           └── observe
├── step[2] ...
└── task.finalize
    └── memory.record_episode
```

### Required span attributes

| Span | Attributes |
|---|---|
| `task` | `task.id`, `task.origin`, `task.status`, `task.plan_version`, `task.replan_count`, `task.tokens`, `task.cost_usd` |
| `plan` | `plan.step_count`, `plan.tool_names`, `plan.model`, `plan.prompt_version`, `plan.schema_retries` |
| `action` | `action.id`, `action.tool`, `action.tool_version`, `action.capability`, `action.attempt`, `action.status` |
| `policy.evaluate` | `policy.decision`, `policy.rule_id`, `policy.version_hash`, `policy.capability` |
| `tool.execute` | `tool.name`, `tool.tier`, `tool.error_code`, `tool.retryable` |
| `verify` | `verify.type`, `verify.outcome`, `verify.observation_tier` |
| `model.complete` | `model.provider`, `model.name`, `model.purpose`, `model.prompt_tokens`, `model.completion_tokens`, `model.ttft_ms`, `model.cache_hit`, `model.sensitivity` |
| `memory.retrieve` | `retrieval.strategy`, `retrieval.k`, `retrieval.hits`, `retrieval.top_score` |

**Never** as attributes: prompt bodies, document contents, file contents, credentials, screenshots. Attributes carry hashes and identifiers; payloads live in the (access-controlled, retention-limited) blob store.

---

## 2. Trace continuity across async boundaries

The hard part. A task's trace must survive the hop from API process → Redis → worker process, and remain intact across a crash and resume.

- Trace context is serialized into the Redis stream entry (W3C `traceparent`) and restored by the worker.
- The root `task` span is a **long-lived** span persisted by `trace_id` in the `tasks` row; workers create child spans linked to it via span links rather than holding an in-memory parent.
- Resumed actions after a crash create a **new span with a link** to the abandoned one, annotated `resumed_after_crash=true` — so the timeline shows the interruption honestly instead of hiding it.

---

## 3. Metrics

Prometheus, exposed at `/metrics`.

### Task level
```
astra_tasks_total{status, origin}                             counter
astra_task_duration_seconds{status}                           histogram
astra_task_steps                                              histogram
astra_task_replans_total                                      counter
astra_task_cost_usd                                           histogram
astra_human_interventions_total{reason}                       counter
astra_task_success_ratio                                      gauge (recording rule)
```

### Action / tool level
```
astra_actions_total{tool, status, capability}                 counter
astra_action_duration_seconds{tool}                           histogram
astra_action_retries_total{tool, error_code}                  counter
astra_actuation_tier_total{tier}                              counter
astra_tool_errors_total{tool, code, retryable}                counter
astra_dead_letters_total{tool}                                counter
```

### Security
```
astra_policy_decisions_total{decision, capability, rule_id}   counter
astra_approvals_total{outcome, capability}                    counter
astra_approval_wait_seconds                                   histogram
astra_privacy_routing_blocks_total                            counter
astra_redactions_total{sink}                                  counter
```

### Verification
```
astra_verifications_total{type, outcome}                      counter
astra_unverified_actions_total{tool}                          counter
astra_verification_duration_seconds{type}                     histogram
```

### Model
```
astra_model_calls_total{provider, model, purpose, cache_hit}  counter
astra_model_tokens_total{provider, model, direction}          counter
astra_model_ttft_seconds{provider, model}                     histogram
astra_model_latency_seconds{provider, model}                  histogram
astra_model_cost_usd_total{provider, model}                   counter
astra_model_failovers_total{from, to, reason}                 counter
astra_circuit_breaker_state{provider}                         gauge
```

### Memory
```
astra_retrievals_total{strategy}                              counter
astra_retrieval_latency_seconds{strategy}                     histogram
astra_ingestion_documents_total{status}                       counter
astra_ingestion_chunks_total                                  counter
astra_context_graph_entities{type}                            gauge
```

### Runtime health
```
astra_queue_depth{stream}                                     gauge
astra_workers_active                                          gauge
astra_leases_reclaimed_total                                  counter
astra_action_queue_wait_seconds                               histogram
```

`astra_leases_reclaimed_total` and `astra_unverified_actions_total` are the two most diagnostic series in the whole system: the first means workers are dying or under-provisioned, the second means the agent is doing things it cannot prove.

---

## 4. Logging

Structured JSON to stdout; `structlog` bound with `task_id`, `step_id`, `action_id`, `trace_id`, `span_id` on every record (FR-803). Levels: `DEBUG` (dev only), `INFO` (state transitions, decisions), `WARNING` (retries, degradation, tier fallback), `ERROR` (failures), `CRITICAL` (invariant violations — policy bypass, state-machine violation, hash-chain break).

Every log record passes through the redaction filter before any sink. No exceptions, including exception tracebacks.

---

## 5. Dashboards (`infra/grafana/`, versioned JSON)

1. **Task Health** — success rate over time, duration percentiles, replan rate, human-intervention rate, cost per task, failures by error code.
2. **Tool Reliability** — per-tool success rate and latency, error-code breakdown, actuation-tier distribution (the vision-fallback ratio trend), dead letters.
3. **Model Performance** — TTFT/latency by provider, tokens and cost over time, cache hit rate, failovers, circuit-breaker state, local-vs-cloud split.
4. **Security** — policy decisions by outcome, approval latency, denials by rule, privacy-routing blocks, L4 attempt count.
5. **Memory** — retrieval latency, hit rates, corpus size, ingestion throughput, graph growth.
6. **Runtime** — queue depth, worker count, lease reclaims, queue wait time, in-flight actions.

---

## 6. Per-task trace view (FR-804)

`astra trace <task_id>` renders the timeline in the terminal:

```
Task 01J8X... "grade submission 482"                    SUCCEEDED   12.8s
├─ plan                                                             1.8s
│  ├─ memory.retrieve (hybrid, k=8, hits=6)                         0.4s
│  └─ model.complete (gpt-5.1, plan, 4.2k→612 tok, ttft 380ms)      1.3s
├─ step 1  read rubric
│  └─ fs.read_file  L0  tier1                           SUCCEEDED   0.1s
├─ step 2  read submission
│  └─ screen.read_active_window  L0  tier2              SUCCEEDED   2.1s
├─ step 3  compute grade
│  └─ model.complete (gpt-5.1, extract, 8.1k→240 tok)   SUCCEEDED   6.2s
└─ step 4  enter grade
   ├─ policy.evaluate  → CONFIRM (rule: gradebook-write)            0.0s
   ├─ approval.wait                                     APPROVED   14.2s  [excluded from task time]
   ├─ desktop.set_field  L2  tier2                      SUCCEEDED   1.5s
   └─ verify (value_equals: 87 == 87, tier2)            PASS        0.8s
```

Approval wait time is tracked separately from execution time. Mixing them would make every human-gated task look pathologically slow and would hide real regressions.

---

## 7. SLOs (self-imposed)

| SLO | Target | Alert |
|---|---|---|
| Task success rate (eval suite) | ≥ 80 % | < 70 % over 20 tasks |
| Verification coverage on ≥L2 actions | 100 % | any gap |
| Unverified action ratio | < 5 % | > 10 % |
| Vision-tier actuation ratio | < 20 % | > 35 % (reliability degrading) |
| p95 action queue wait | < 2 s | > 10 s |
| Lease reclaims | ~0 in steady state | > 5/min |
| Model p95 latency (cloud) | < 8 s | > 20 s |
| Cost per task | < $0.25 | > $1.00 |
| Privacy routing blocks | 0 | any (means a routing bug) |
