# 02 — Architecture

Status: **Approved baseline (v1.0)**

---

## 1. Design forces

The architecture is shaped by five forces, in tension:

1. **Probabilistic core, deterministic obligations.** LLM output is unreliable; task state must not be.
2. **Long-running work.** A task may run 30+ minutes across process restarts. Request/response is not a viable execution model.
3. **Dangerous side effects.** Actions touch real files, real email, real money. Security cannot be a late layer.
4. **Heterogeneous actuators.** APIs, browsers, and native GUIs have wildly different reliability. The runtime must not care which is which.
5. **Everything must be measurable.** If it can't be traced and scored, it can't be improved or claimed.

---

## 2. Component map

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              CLIENTS                                     │
│   vyomel CLI    │   HTTP/WS API consumers   │  (later) desktop UI, voice  │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │  REST + WebSocket
┌──────────────────────────────▼───────────────────────────────────────────┐
│                          vyomel.api  (FastAPI)                            │
│   routers: tasks, approvals, memory, tools, traces, admin, health        │
│   responsibilities: validation, auth, streaming, NO business logic       │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────────────┐
│                       vyomel.orchestrator                                 │
│   TaskService · PlanService · ApprovalService                            │
│   Owns transactional writes. The only layer allowed to mutate task state │
│   through the repositories.                                              │
└───────┬───────────────────┬───────────────────────┬──────────────────────┘
        │                   │                       │
┌───────▼────────┐ ┌────────▼─────────┐ ┌───────────▼──────────┐
│ vyomel.planner  │ │ vyomel.security   │ │  vyomel.memory        │
│ decomposition  │ │ capability model │ │  context graph       │
│ DAG builder    │ │ policy engine    │ │  ingestion pipeline  │
│ replanner      │ │ approval gate    │ │  hybrid retriever    │
│ budget guard   │ │ audit writer     │ │  episodic recorder   │
└───────┬────────┘ └────────┬─────────┘ └───────────┬──────────┘
        │                   │                       │
        └───────────────────┴───────┬───────────────┘
                                    │
┌───────────────────────────────────▼──────────────────────────────────────┐
│                          vyomel.runtime                                   │
│   Dispatcher → Redis Streams  │  Worker pool  │  Lease reaper            │
│   Action state machine · retry/backoff · idempotency · compensation      │
└───────┬──────────────────────────────────────────────┬───────────────────┘
        │                                              │
┌───────▼────────────────┐                  ┌──────────▼───────────────────┐
│    vyomel.tools         │                  │    vyomel.verify              │
│  registry + contracts  │                  │  postcondition assertions    │
│  ┌──────────────────┐  │                  │  value/element/file/api/llm  │
│  │ fs · shell · web │  │                  │  evidence capture            │
│  │ browser (CDP)    │  │                  └──────────────────────────────┘
│  │ desktop (UIA)    │  │
│  │ api (gmail/cal)  │  │                  ┌──────────────────────────────┐
│  │ memory · media   │  │                  │    vyomel.models              │
│  └──────────────────┘  │◄─────────────────┤  provider abstraction        │
└────────────────────────┘                  │  router (privacy/cost/perf)  │
                                            │  token+cost accounting       │
┌──────────────────────────────────────────┐│  response cache              │
│    vyomel.perception                      │└──────────────────────────────┘
│  screen capture · UIA tree · DOM · OCR   │
│  active window · clipboard · selection   │  ┌─────────────────────────────┐
└──────────────────────────────────────────┘  │   vyomel.obs                 │
                                              │  tracing · metrics · logs   │
┌──────────────────────────────────────────┐  └─────────────────────────────┘
│               PERSISTENCE                │
│  Postgres 17 + pgvector  (source of truth)│  Redis 7 (transport + cache)  │
└──────────────────────────────────────────┘
```

---

## 3. Layer responsibilities and rules

| Layer | Owns | May depend on | Must never |
|---|---|---|---|
| `api` | HTTP/WS contract, request validation, auth, SSE streaming | `orchestrator`, `obs` | contain business logic or touch the DB directly |
| `orchestrator` | Use cases, transactions, task lifecycle | `planner`, `security`, `memory`, `runtime`, `store`, `tools` (catalog) | perform I/O against tools directly |
| `planner` | NL → DAG, replanning, budget estimation | `models`, `memory`, `tools` (catalog only) | execute anything |
| `security` | Capability classification, policy, approvals, audit, redaction | `store` | be bypassable — every action path goes through it |
| `memory` | Ingestion, embedding, graph, retrieval, episodic records | `models` (embeddings), `store` | store structured state as free-form vectors |
| `runtime` | Dispatch, worker loop, state machine, retries, leases | `tools`, `verify`, `security`, `store` | make planning decisions |
| `tools` | Actuation, structured errors | `perception`, `models` (for vision tools) | mutate task state |
| `verify` | Postcondition assertions, evidence | `perception`, `tools` (read-only), `models` | perform side effects |
| `models` | Provider abstraction, routing, accounting | — | leak sensitive data to cloud (FR-703) |
| `perception` | Environment observation | — | act |
| `store` | Repositories, migrations, unit-of-work | — | contain domain rules |
| `obs` | Tracing/metrics/logging plumbing | — | depend on any other Vyomel layer |

**Dependency rule:** dependencies point downward only. `scripts/check_layering.py` enforces this by static import analysis in CI. A cyclic or upward import fails the build.

---

## 4. The canonical request lifecycle

```
  1. POST /v1/tasks  { instruction, context_hints }
        │
  2. TaskService.create()  ──► Postgres: tasks(status=PLANNING)   [committed]
        │
  3. PlanService.plan()
        ├─ memory.retrieve(instruction)      → context bundle + citations
        ├─ tools.catalog_for(principal)      → capability-filtered tool defs
        ├─ models.route(sensitivity, complexity) → provider choice
        └─ LLM structured output             → Plan{steps[], edges[]}
        │
  4. Plan validated (schema + DAG acyclicity + tool existence + budget)
        └─ Postgres: steps[], actions[] (status=PLANNED)          [committed]
        │
  5. Dispatcher: for each action with satisfied deps
        ├─ security.classify(action)         → capability level
        ├─ security.evaluate(policy)         → ALLOW | CONFIRM | DENY
        │     CONFIRM → status=WAITING_FOR_USER, emit approval, STOP
        │     DENY    → status=FAILED(permission_denied)
        └─ ALLOW → XADD vyomel:actions  (Redis Stream)             [dispatched]
        │
  6. Worker XREADGROUP → claims action with a lease
        ├─ Postgres: status=RUNNING, lease_until=now+timeout      [committed]
        ├─ idempotency check: has this key already succeeded?
        ├─ tools.invoke(tool, params)        → ToolResult
        └─ Postgres: result persisted                             [committed]
        │
  7. verify.assert_postconditions(action)
        ├─ PASS      → status=SUCCEEDED
        ├─ FAIL      → status=FAILED(verification_failed)
        └─ NO_METHOD → status=UNVERIFIED     (never SUCCEEDED)
        │
  8. security.audit(action, decision, evidence)  [append-only]
        │
  9. Dispatcher re-evaluates the DAG
        ├─ ready actions → back to step 5
        ├─ failure + retries left → backoff, requeue
        ├─ failure + replans left → planner.replan(failure_context)
        └─ all terminal → tasks(status=SUCCEEDED|FAILED|NEEDS_HUMAN)
```

Note the commit points. Every transition that could be lost on a crash is committed to Postgres **before** the effect is attempted, and the idempotency check at step 6 makes replay safe. This is what satisfies FR-202 / NFR-03.

Step 5 is implemented as `vyomel/runtime/gate.py` rather than inside `vyomel/security/`. The decision needs both halves — a policy verdict and the action state machine — and the layering rule forbids `security` from importing `runtime`. So `security` owns records and verdicts, and the gate is the single component holding both. The practical benefit is that classification, policy, and approval records are all testable without a scheduler.

---

## 5. Why Postgres-as-truth + Redis-as-transport

An alternative is a full workflow engine (Temporal, Prefect). Rejected — see `adr/ADR-0003`. Summary:

- **Learning value:** building the durable execution layer *is* the distributed-systems content of this project. Delegating it removes the most interesting part.
- **Honesty:** the resume says "Redis-backed task queues, durable asynchronous execution." That should mean code the author wrote and can explain under questioning.
- **Fit:** the workload is a single-user DAG executor, not a million-workflow cluster. Temporal's operational weight is unjustified.

The correctness argument:

| Failure | Behavior |
|---|---|
| Worker dies mid-action | Lease expires → reaper returns the action to `READY` → another worker replays → idempotency key prevents duplicate effect. |
| Redis loses the stream | Postgres still holds every `READY`/`RUNNING` action; the dispatcher rebuilds the stream on startup. Redis is a cache of intent, never the record. |
| Postgres unavailable | System refuses to dispatch. Fail closed (principle 4). |
| Duplicate delivery | Consumer group + `idempotency_key` unique constraint makes the second execution a no-op returning the first result. |
| Poison action | `attempt_count` cap → `FAILED` → dead-letter table with full context. |

---

## 6. Perception and actuation hierarchy

Enforced in code, not left to the model:

```
   1. Native API          (Gmail API, GitHub API, filesystem, AppleScript/COM)
        ↓ unavailable
   2. Accessibility tree  (Windows UIA / macOS AX / browser a11y tree)
        ↓ unavailable
   3. DOM selectors       (CDP / Playwright)
        ↓ unavailable
   4. Vision + coordinates (screenshot → VLM → click x,y)
```

Each `Actuator` declares the tier it operated at. The metric `vyomel_actuation_tier_total{tier=...}` makes degradation visible: a rising vision ratio is an early warning that reliability is dropping. Tier 4 is always ≥ L2 capability because coordinate clicking has an unbounded blast radius.

---

## 7. Deployment topologies

### Dev (this machine)

```
Windows host                       WSL2 Ubuntu (Docker)
┌──────────────────────┐          ┌───────────────────────────┐
│ vyomel api  :8080     │          │ postgres+pgvector :55432  │
│ vyomel worker (n=2)   │◄────────►│ redis            :56379   │
│ vyomel cli            │ localhost│ (M10) otel-collector,     │
│ pytest / evals       │ forward  │       prometheus, grafana │
└──────────────────────┘          └───────────────────────────┘
        │
        └──► optional: llama.cpp/Ollama :11434 (local model)
        └──► optional: SSH tunnel → rented GPU vLLM :8000
```

### Target (M13, Kubernetes) — implemented

```
  Ingress ─► vyomel-api Deployment (HPA on RPS optional)
                 │
                 ├─► vyomel-worker Deployment (HPA on queue depth)
                 ├─► vyomel-scheduler Deployment (Redis leader-elected)
                 │
                 ├─► Postgres StatefulSet (pgvector) + PVC
                 ├─► Redis StatefulSet + PVC
                 └─► vLLM StatefulSet (GPU node pool; optional)

  Host: vyomel agent ──WebSocket──► API  (desktop.* only; ADR-0009)
```

Chart: `infra/helm/vyomel/`. kind profile: `values-kind.yaml`. Failover: chart README.

---

## 8. Technology decisions

| Concern | Choice | Rationale | ADR |
|---|---|---|---|
| Language | Python 3.13 | Ecosystem for LLM/agent/vector work; matches resume claim | 0001 |
| API | FastAPI + Pydantic v2 | Async, native OpenAPI, schema validation doubles as tool contracts | 0001 |
| DB | Postgres 17 + pgvector | One system for relational state *and* vectors; avoids a second datastore | 0002 |
| Migrations | Alembic | Standard, reversible, testable | 0002 |
| ORM | SQLAlchemy 2.0 async | Mature async, explicit unit-of-work | 0002 |
| Queue | Redis 7 Streams + consumer groups | At-least-once + explicit ack + lease semantics; author-owned durability logic | 0003 |
| Browser | Playwright (Python) | CDP access, a11y tree, no system Node needed | 0004 |
| Desktop | `uiautomation`/`pywinauto` + `mss` | Windows UIA is the structured path; screen capture for the vision fallback | 0005 |
| Model serving | Provider abstraction; vLLM on rented GPU; llama.cpp local | Local GPU is 2 GB — see `13-ENVIRONMENT.md` C-1 | 0006 |
| Embeddings | `bge-small-en-v1.5` local (384-d), pluggable | Runs on CPU, strong retrieval quality per compute, keeps documents local | 0007 |
| Observability | OpenTelemetry → Prometheus + Jaeger, Grafana | Matches the author's Microsoft OTel work; industry standard | 0008 |
| Packaging | `pyproject.toml`, `uv`-compatible, `ruff` + `mypy` | Modern, fast, strict | 0001 |

---

## 9. Cross-cutting concerns

| Concern | Mechanism |
|---|---|
| Configuration | `pydantic-settings`, layered: defaults → `vyomel.toml` → `.env` → env vars → CLI flags |
| Errors | Single `VyomelError` hierarchy with stable `code`, `retryable`, and `user_message` fields |
| IDs | ULIDs — sortable by creation time, URL-safe, collision-resistant |
| Time | UTC everywhere; `timestamptz` in Postgres; a single injectable `Clock` for testable time |
| Serialization | Pydantic models at every boundary; no bare dicts crossing layers |
| Concurrency | `asyncio` throughout; blocking actuators (UIA, FFmpeg) run in a bounded thread pool |
| Secrets | OS keyring for OAuth tokens; `.env` for API keys; never in DB, logs, or prompts |
| Caching | Redis for model responses (deterministic mode), retrieval results, and UIA tree snapshots |
