# 12 — Roadmap

Status: **Approved baseline (v1.0)**

---

## 1. Sequencing principle

**Vertical slices, not horizontal layers.** Every milestone ends with something that runs end-to-end and is demonstrable. Building "all of the data layer," then "all of the planner," then "all of the tools" would mean months with nothing working, and a high chance of building the wrong abstractions.

Second principle: **the durable execution engine comes before the fancy actuators.** A reliable runtime with three boring tools is worth far more than twelve tools on a runtime that loses work. It is also the harder and more distinctive engineering.

Estimates assume ~10–15 focused hours/week alongside a full-time job and coursework.

---

## 2. Phase overview

| Phase | Milestones | Outcome |
|---|---|---|
| **I — Foundation** | M0–M3 | A durable, permission-gated, verified agent with local tools. Resume-truthful for the core claim. |
| **II — Knowledge** | M4–M6 | RAG, real planning, long-running durability under chaos. |
| **III — Actuation** | M7–M9 | Browser, desktop, external APIs. "Multi-application execution" becomes true. |
| **IV — Infrastructure** | M10–M13 | Observability, model serving, evaluation, Kubernetes. |
| **V — Frontier** | M14–M17 | Media, workflow learning, voice, multimodal verticals. |

---

## 3. Milestones

### M0 — Foundation and skeleton *(week 1)*

Repo scaffold, tooling, infrastructure, and the first vertical slice through every layer.

- `pyproject.toml`, `ruff`, `mypy --strict`, `pytest`, pre-commit, GitHub Actions CI
- Postgres+pgvector and Redis running in WSL Docker; Alembic wired
- Config, structured logging, error hierarchy, ULID ids, `Clock`
- FastAPI with `/healthz`, `/readyz`, `/version`, `/metrics`
- `tasks` table + `POST /v1/tasks` + `GET /v1/tasks/{id}`
- CLI: `astra serve`, `astra doctor`, `astra db upgrade`
- `scripts/check_layering.py` and `scripts/check_traceability.py` in CI from day one

**Exit:** `astra doctor` all-green; `POST /v1/tasks` persists and survives a restart; CI green.

---

### M1 — Execution runtime *(weeks 2–3)* — **the keystone** — complete (2026-08-28)

State machines, schema, tools, dispatcher write-ordering, worker, reaper, timeout handling, and handwritten DAG execution are in tree, with `demos/m1/` reproducing the 5-action DAG and a mid-flight worker kill. The one deferral is an OS-level `kill -9` of a live worker process, which needs the M6 chaos harness; the lease-expiry replay test is the M1 stand-in.

- Full schema: `steps`, `step_edges`, `actions`, `dead_letters`
- Action state machine with illegal-transition rejection (FR-203)
- Redis Streams dispatch, consumer groups, leases, `XAUTOCLAIM`
- Worker process, lease reaper, startup recovery
- Retry with exponential backoff + jitter; idempotency keys; `side_effect_ledger`
- DAG readiness evaluation and bounded parallelism
- Three trivial tools: `fs.read_file`, `fs.list_dir`, `task.report`
- Hand-written plans only — **no LLM yet**

**Exit:** a hand-written 5-action DAG executes correctly; `kill -9` a worker mid-DAG and the task still completes exactly once; `tests/runtime/` green including crash-recovery.

*Why no LLM yet:* debugging a nondeterministic planner on top of an unproven runtime is how these projects die. The runtime must be trustworthy before anything probabilistic sits on it.

---

### M2 — Security and permissions *(week 4)* — complete (2026-08-28)

Classification, policy, approvals, audit, redaction, and the CLI/API surfaces are in tree and green. Two items in the security document are parsed but deliberately unused until the layer that needs them exists: the egress allowlist (first network tool, M4) and `policy.sensitivity`, which gates model routing (FR-703, M4). Prompt-injection defense currently stops at untrusted-content taint escalation — boundary markers arrive with the planner in M5.

- Capability classification with escalation rules
- Policy engine, `config/policy.yaml`, default-deny, L4 invariant
- Approval flow: creation, `WAITING_FOR_USER`, decide, modify, expiry
- Append-only hash-chained audit log + `astra audit verify`
- Redaction filter across logs/traces/audit; `Secret` type
- Filesystem sandbox (allowlisted roots, traversal rejection)
- CLI: `astra approvals`, `astra approve/reject/modify`

**Exit:** `tests/security/` green including adversarial policy fuzzing; an L3 action visibly blocks for approval and proceeds only after decision.

---

### M3 — Verification and first real tools *(week 5)* — in progress (2026-08-28)

The engine, the `UNVERIFIED` task-completion tightening, `verifications` persistence, mutating fs tools, `shell.run`, `git.*`, cancel compensation, operator CLI (`astra tools` / `astra do` / `astra show`), and cooperative cancel of `RUNNING` actions are in tree.

- `astra.verify` with `value_equals`, `file_exists`, `file_hash` re-observing; `api_readback`, `llm_judge` registered as `NO_METHOD` until their paths exist
- `UNVERIFIED` status wired end-to-end; no path to task `SUCCEEDED` without verification unless the step opts in
- Mutating tools: `fs.write_file`, `fs.move`, `fs.copy`, `fs.delete` (trash-based, L4 for directory trees), `shell.run` (allowlisted), `git.status` / `git.diff` / `git.commit` / `git.push`
- Compensation: `Canceller` calls `tool.compensate()` in `reverse_topo` order; `POST /v1/tasks/{id}/cancel` and `astra cancel`

**Exit:** injected wrong-value writes are caught 100 % of the time; cancel compensates reversible actions in reverse topological order.

---

### M4 — Memory and RAG *(weeks 6–7)*

- Ingestion pipeline (pdf/docx/md/txt/html/code), structure-aware chunking
- Local embeddings (`bge-small-en-v1.5`), HNSW + tsvector indexes
- Hybrid retrieval with RRF; citations with precise offsets
- Context graph: entities, relations, salience decay
- Episodic memory recording
- Tools: `memory.query`, `memory.get_entity`, `memory.remember`, `memory.forget`
- `evals/suites/rag/` with the synthetic corpus and 100 labeled questions

**Exit:** `recall@10 ≥ 0.85` on the benchmark; first ablation table committed.

**Done (2026-09-02):** ingest (md/txt/html/pdf/docx), bge embedder + hashing fallback,
HNSW + tsvector hybrid RRF, context graph (entities/remember/forget), episodic memory,
four memory tools, eval corpus (100 docs / 125 questions), recall@10 = 0.928 with
ablation table in `evals/results/2026-09-02-m4/`. Still deferred: watch/async jobs,
code/csv extractors, salience decay, graph expansion in retrieval.

---

### M5 — Planner *(weeks 8–9)* — done (2026-09-02)

**Exit:** natural language → executed plan end-to-end; `tool_call_accuracy` and `task_completion_rate` measured and committed for ≥ 2 model configurations.

Shipped: mock + mock-alt planners, OpenAI-compatible adapter, router with privacy block, `model_calls` accounting, deterministic cache, decompose/replan with schema retries, step contracts, token budget gate, replan runtime hook, boundary markers, `POST /reply`, agent eval (100 tasks, 1.0/1.0 on both configs). See `evals/results/2026-09-02-m5/`.

Deferred: cloud provider production tuning, FR-705 failover/circuit breaker, `evals/harness/` full runner, prompt-injection boundary markers in all model paths.

---

### M6 — Durability under chaos *(week 10)* — done (2026-09-02)

**Exit:** 20 minutes of random `kill -9` every 60 s with **0** lost or duplicated side effects.

Shipped: heartbeat lease extension, blob spill, `web.fetch_mock`, 100-item research plan builder, `evals/suites/longrun/` harness (`fast` / `standard` / `chaos` modes), integration tests for simulated kills and Redis flush recovery. See `evals/results/2026-09-02-m6/`.

Deferred: OS-level `kill -9` subprocess harness (DB-state injection is the portable stand-in, same as M1 demo); full 20-minute chaos run is manual via `--mode chaos`.

---

### M7 — Browser agent *(weeks 11–12)* — done (2026-09-02)

**Exit:** ≥ 80 % success on 40 browser workflows; measured actuation-tier distribution.

Shipped: Playwright integration (optional backend), persistent profile dir, accessibility-first element resolver (tier 2 → DOM 3 → coordinates 4), full `05` §3.3 tool surface, HTML fixtures with perturbation variant, `evals/suites/browser/` (40 workflows, 1.0 success on fixture backend). See `evals/results/2026-09-02-m7/`.

Deferred: Playwright-backed eval in default CI image; `element_exists` verifier for live pages.

---

### M8 — Desktop agent *(weeks 13–15)*

- Windows UIA backend (`uiautomation`/`pywinauto`), screen capture (`mss`), OCR fallback
- Tier-enforcing `DesktopActuator.resolve()`
- Vision fallback via a VLM, with credential-region redaction before egress
- Fixture WinForms app + `evals/suites/desktop/` including UI perturbation

**Exit:** ≥ 70 % success on 50 desktop workflows; `verification_catch_rate` 100 %; vision-tier ratio < 30 %.

---

### M9 — External API tools *(week 16)*

- OAuth flows with least-privilege scopes, keyring token storage, refresh rotation
- Gmail, Google Calendar, GitHub tools
- Scenario S3 ("find the interview email, check calendar, propose prep blocks") end-to-end

**Exit:** S3 runs end-to-end with correct approval gating on every L3 action.

---

### M10 — Observability *(week 17)*

- OpenTelemetry tracing across process boundaries, including resume-after-crash span links
- Full Prometheus metric set
- OTel Collector + Prometheus + Grafana + Jaeger in WSL Docker
- Six versioned Grafana dashboards
- `astra trace <task_id>` terminal renderer

**Exit:** a single task's full lifecycle is visible in Jaeger; all six dashboards populated.

---

### M11 — Model serving and benchmarking *(week 18)*

- vLLM provider + `infra/vllm/` deployment artifacts
- Rented-GPU benchmark session; `evals/suites/serving/` results committed
- Local llama.cpp path validated fully offline (NFR-12)
- Router tuned from measured data

**Exit:** committed vLLM-vs-baseline throughput/latency table with reproduction commands.

---

### M12 — Evaluation maturity *(week 19)*

- `evals/harness/compare.py` regression gating in CI
- `evals/suites/security/` complete, `injection_success_rate` = 0
- Full ablation tables for RAG, planner models, and routing strategies
- Public results dashboard in the README

**Exit:** CI blocks a PR that regresses any gated metric.

---

### M13 — Kubernetes *(week 20)*

- Helm chart: api, worker, scheduler, postgres, redis, vLLM StatefulSet
- HPA on queue depth; leader election for the scheduler
- Local-agent split for host-bound desktop tools (`adr/ADR-0009`)
- Validated on `kind` in CI and once on a short-lived cloud cluster

**Exit:** `helm install` produces a working deployment; documented failover behavior.

---

### M14–M17 — Frontier *(post-v1)*

| M | Scope |
|---|---|
| **M14** | Media plugin: transcription, segment detection, cut/mute/caption, FFmpeg pipeline (scenario S7) |
| **M15** | Workflow learning: sequence mining, parameterization, proposal UX (FR-901–903) |
| **M16** | Voice: local Whisper STT, wake word, TTS, barge-in |
| **M17** | Multimodal verticals: camera perception, the gym scenario (S8), wearable client against the same API |

---

## 4. Definition of done (every milestone)

1. All new P0 requirements have linked tests; `check_traceability.py` passes.
2. `ruff`, `mypy --strict`, and the full test suite pass in CI.
3. Coverage ≥ 85 % on `core`, `runtime`, `security`.
4. Docs updated in the same PR as the code — never after.
5. Any measured claim has a committed `evals/results/` entry.
6. A demo script under `demos/` reproduces the milestone's headline capability from a clean checkout.
7. `CHANGELOG.md` updated; milestone tagged `mN`.

---

## 5. Critical path and risk ordering

```
M0 ──► M1 ──► M2 ──► M3 ──► M5 ──► M6 ──► M7 ──► everything else
       ▲ hardest, highest value, must not be rushed
                     M4 can proceed in parallel with M2/M3
```

The riskiest milestones are **M1** (correctness of the durable engine) and **M8** (desktop reliability). M1 is scheduled first because everything depends on it. M8 is scheduled late because its success rate is bounded by external factors (application UIA quality) and it must not block the parts that are fully within our control.

**If time runs short, cut in this order:** M17 → M16 → M15 → M14 → M13 → M9. Never cut M2, M3, M6, or M12 — permissions, verification, durability, and evaluation are the differentiators. A demo without them is just another agent wrapper.
