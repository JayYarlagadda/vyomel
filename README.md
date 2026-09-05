# Vyomel — Personal AI Execution Platform

> A personal AI execution layer that turns natural-language intent into **verified, permission-aware actions** across a user's digital environment.

Vyomel is not a chatbot. A chatbot returns text. Vyomel changes state in the world — files, calendars, browsers, desktop applications, third-party APIs — and then **proves the change actually happened** before reporting success.

**Status:** M17 complete — roadmap M0–M17 finished. See [`docs/12-ROADMAP.md`](docs/12-ROADMAP.md) and [`docs/17-BUILD-LOG.md`](docs/17-BUILD-LOG.md).

---

## The thesis

Building an agent that clicks a button is easy and already commoditized. The unsolved problems are:

| Problem | Vyomel's answer |
|---|---|
| Agents lose work when a process dies | Postgres as source of truth, Redis Streams as transport, leases + reapers + idempotency keys. Chaos-tested. |
| Agents claim success they can't prove | Mandatory post-action re-observation. `UNVERIFIED` is a first-class state, so the system never has to lie. |
| Agents are given all-or-nothing trust | A capability lattice (L0–L4) enforced in the runtime, with an invariant that L4 can never be auto-approved. |
| Agents forget everything | A persistent personal context graph plus hybrid retrieval with precise citations. |
| Agents leak private data to the cloud | Sensitivity classification with hard local-only routing that fails closed rather than escalating. |
| Agents can't be improved because nothing is measured | A reproducible evaluation harness with ablations and CI regression gates. |

Agents are a distributed systems problem wearing an AI costume. Vyomel is built on that premise.

---

## Architecture

```
        intent (text · voice · screen · camera)
                        │
                Intent Interface  ──►  Context & Memory  ──►  Agent Planner
                                          (graph + RAG)         (NL → task DAG)
                                                                     │
                                    Permission Engine ◄──────────────┤
                                    (capability L0–L4)                │
                                                                     ▼
                                                          Execution Runtime
                                                    (durable · resumable · bounded)
                                                                     │
                              ┌──────────────────────────────────────┼─────────────────┐
                              ▼                                      ▼                 ▼
                        Browser Agent                          Desktop Agent      API Agents
                        (a11y → DOM → px)                      (UIA → px)         (Gmail/Cal/GH)
                              └──────────────────────────────────────┼─────────────────┘
                                                                     ▼
                                                          Verification Engine
                                                        (re-observe · assert · PASS/FAIL)

  Cross-cutting: OpenTelemetry · Audit trail (hash-chained) · Evaluation harness · Model router
```

Full detail in [`docs/02-ARCHITECTURE.md`](docs/02-ARCHITECTURE.md).

---

## Documentation

Read in this order:

| Doc | What it covers |
|---|---|
| [`00-OVERVIEW`](docs/00-OVERVIEW.md) | Vision, scope, explicit non-goals, differentiation |
| [`01-REQUIREMENTS`](docs/01-REQUIREMENTS.md) | Numbered FR/NFR with test traceability |
| [`02-ARCHITECTURE`](docs/02-ARCHITECTURE.md) | Components, layering rules, request lifecycle |
| [`03-DATA-MODEL`](docs/03-DATA-MODEL.md) | Postgres schema, pgvector layout, migration policy |
| [`04-API-SPEC`](docs/04-API-SPEC.md) | HTTP/WebSocket contract and CLI surface |
| [`05-TOOL-SPEC`](docs/05-TOOL-SPEC.md) | Tool contract and full catalog |
| [`06-SECURITY-PERMISSIONS`](docs/06-SECURITY-PERMISSIONS.md) | Threat model, capability lattice, policy, approvals |
| [`07-EXECUTION-ENGINE`](docs/07-EXECUTION-ENGINE.md) | State machines, durability, retries, bounded autonomy |
| [`08-MEMORY-RAG`](docs/08-MEMORY-RAG.md) | Context graph, ingestion, hybrid retrieval |
| [`09-MODEL-SERVING`](docs/09-MODEL-SERVING.md) | Provider abstraction, routing, vLLM plan |
| [`10-OBSERVABILITY`](docs/10-OBSERVABILITY.md) | Traces, metrics, dashboards, SLOs |
| [`11-EVALUATION`](docs/11-EVALUATION.md) | Benchmark suites and metric definitions |
| [`12-ROADMAP`](docs/12-ROADMAP.md) | Milestones M0–M17 with exit criteria |
| [`13-ENVIRONMENT`](docs/13-ENVIRONMENT.md) | Verified machine facts, constraints, setup runbook |
| [`14-RESUME-MAPPING`](docs/14-RESUME-MAPPING.md) | Every claim → the artifact that proves it |
| [`15-RISKS`](docs/15-RISKS.md) | Risk register |
| [`16-WORKFLOW`](docs/16-WORKFLOW.md) | Dev workflow, definition of done |
| [`17-BUILD-LOG`](docs/17-BUILD-LOG.md) | Session notes: what was built, where it stopped |
| [`adr/DECISIONS`](docs/adr/DECISIONS.md) | Architecture decision records |

---

## Quick start

Prerequisites: Windows 11, Python 3.13, WSL2 with Docker. Full runbook in [`docs/13-ENVIRONMENT.md`](docs/13-ENVIRONMENT.md) §4.

```powershell
# 1. infrastructure (Postgres + pgvector, Redis) inside WSL
wsl -e bash -lc "cd /mnt/d/Vyomel/infra && docker compose up -d"

# 2. environment
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env      # then fill in API keys

# 3. verify + migrate
.\.venv\Scripts\python.exe -m vyomel.cli doctor
.\.venv\Scripts\python.exe -m vyomel.cli db upgrade

# 4. run
.\.venv\Scripts\python.exe -m vyomel.cli serve      # API + scheduler
.\.venv\Scripts\python.exe -m vyomel.cli worker     # in another shell
```

What works today (M3). Natural-language planning (`vyomel do` without `--plan`) is M5; until then tasks carry handwritten plans:

```powershell
# see the 5-action DAG execute, then survive a worker being killed mid-flight
.\.venv\Scripts\python.exe demos\m1\run_demo.py

# create a task; --plan installs a handwritten DAG; --dry-run classifies without dispatch
vyomel do "list the docs" --plan .\plan.json
vyomel show <task_id>
vyomel tasks --status running
vyomel cancel <task_id>

# inspect and (policy-gated) invoke a single tool
vyomel tools list
vyomel tools show fs.write_file
vyomel tools invoke task.report --json '{\"summary\": \"ok\"}'

# the human-in-the-loop surface
vyomel approvals                                     # what is waiting on you
vyomel approve <approval_id>
vyomel modify <approval_id> --set value=85           # re-validated and re-classified
vyomel reject <approval_id> --reason "wrong student"

# ask the policy what it would do, without running anything
vyomel policy test fs.read_file '{\"path\": \"D:/Vyomel/.env\"}'
vyomel policy show

# the audit trail, and proof it has not been altered
vyomel audit tail --task <task_id>
vyomel audit verify

# semantic memory (ingest, query, entities, episodes)
vyomel memory ingest .\notes.md
vyomel memory query "ZX9QUNIQUE failover"
vyomel memory remember "prefers dark mode" --type preference
vyomel memory episodes --limit 10
```

---

## Measured results

No number appears here unless a script in `evals/` reproduces it. Results land as milestones complete. CI job `eval-gate` blocks PRs that regress gated metrics (`evals/harness/compare.py` vs `evals/results/baselines/gated.json`).

| Metric | Target | Current | Source |
|---|---|---|---|
| Task completion rate (100 multi-step tasks) | ≥ 80 % | **1.000** (mock-v1 / mock-v2) | `evals/results/2026-09-02-m5/` |
| Tool-call accuracy | ≥ 80 % | **1.000** (mock planner) | `evals/results/2026-09-02-m5/` |
| Retrieval recall@10 (hybrid) | ≥ 0.85 | **0.928** (hashing-384) | `evals/results/2026-09-02-m4/` |
| RAG ablation (hybrid / lexical / vector) | — | **0.928 / 0.920 / 0.152** | `evals/results/2026-09-04-m12/` |
| Verification catch rate on injected faults | 100 % | **1.000** | `evals/results/2026-09-02-m8/` |
| Lost/duplicated actions under chaos (fast) | 0 | **0** | `evals/results/2026-09-02-m6/` |
| Prompt-injection success rate | 0 | **0.000** (105 cases) | `evals/results/2026-09-04-m12/` |
| vLLM throughput vs sequential (fixture, c32) | — | **~2×** tok/s | `evals/results/serving/` |
| Media S7 (12 clips → 60s draft + mute + caption) | success | **true** (fixture) | `evals/results/2026-09-04-m14/` |
| Workflow learning (mine ≥3 → accept → invoke) | success | **true** | `evals/results/2026-09-04-m15/` |
| Voice wake → speak → barge-in | success | **true** (fixture) | `evals/results/2026-09-04-m16/` |
| Gym S8 (equipment → session plan) | success | **true** (fixture) | `evals/results/2026-09-04-m17/` |

---

## Tech stack

**Core** Python 3.13 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 async · Alembic
**Data** PostgreSQL 17 + pgvector (HNSW) · Redis 7 Streams
**AI** OpenAI-compatible providers · Anthropic · vLLM · llama.cpp · `bge-small-en-v1.5` embeddings
**Actuation** Playwright (CDP + accessibility tree) · Windows UI Automation · FFmpeg
**Observability** OpenTelemetry · Prometheus · Grafana · Jaeger
**Infra** Docker · Kubernetes · Helm · GitHub Actions

---

## License

MIT — see [`LICENSE`](LICENSE).
