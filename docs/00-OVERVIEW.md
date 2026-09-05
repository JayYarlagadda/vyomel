# 00 — Overview, Vision, and Scope

Status: **Approved baseline (v1.0)**
Owner: Jayanth Sai Yarlagadda
Last updated: 2026-08-28

---

## 1. One-sentence definition

> **Vyomel is a personal AI execution layer that turns natural-language intent into verified, permission-aware actions across a user's digital environment.**

Vyomel is not a chatbot. A chatbot returns text. Vyomel **changes state in the world** — files, calendars, browser sessions, desktop applications, third-party APIs — and then **proves** that the change actually happened.

---

## 2. Why this project exists

Three motivations, in priority order:

1. **Career artifact.** The resume already claims Vyomel (see `14-RESUME-MAPPING.md`). Every claim must map to running, tested, measurable code. This document set exists so that no claim is aspirational at interview time.
2. **Technical depth.** The interesting problems here are not "make an LLM click a button." They are: durable distributed execution, capability-based security, retrieval quality, verification of probabilistic actions, and inference routing. These are systems problems with an AI surface — which matches the author's background (distributed systems, Kubernetes, OpenTelemetry, Rust/Go/C# runtime work).
3. **Real product category.** Computer-using agents (OpenAI Operator/CUA lineage, Rabbit DLAM, Perplexity's local-first agent, and a wave of desktop-agent startups) are an active, unsolved category. The unsolved parts are reliability, permissions, persistent context, cross-app execution, privacy, and verification — not tool-calling itself.

---

## 3. What Vyomel is

A layered runtime:

```
                              USER
              voice / text / screen / camera / wearable
                                |
                                v
                    +-----------------------+
                    |   Intent Interface    |   CLI, HTTP API, later: desktop UI, voice
                    +-----------+-----------+
                                |
                                v
                    +-----------------------+
                    |  Context & Memory     |   personal context graph
                    |  tasks | files        |   structured state  -> Postgres
                    |  people | preferences |   semantic memory   -> pgvector
                    |  app state | history  |
                    +-----------+-----------+
                                |
                                v
                    +-----------------------+
                    |    Agent Planner      |   NL -> task DAG
                    |  decomposition        |   tool selection
                    |  replanning (bounded) |
                    +-----------+-----------+
                                |
                +---------------+---------------+
                |                               |
                v                               v
      +-------------------+          +---------------------+
      | Permission Engine |          |  Retrieval Layer    |
      | capability L0-L4  |          |  hybrid RAG         |
      | approval gates    |          |  BM25 + vector      |
      +---------+---------+          +----------+----------+
                |                               |
                +---------------+---------------+
                                |
                                v
                    +-----------------------+
                    |  Execution Runtime    |   durable, resumable, retryable
                    |  action state machine |
                    +-----------+-----------+
                                |
        +-----------------------+-----------------------+
        |                       |                       |
        v                       v                       v
   Desktop Agent          Browser Agent            API Agents
   UIA / AppleScript      CDP / Playwright         Gmail, Calendar, GitHub
        |                       |                       |
        +-----------------------+-----------------------+
                                |
                                v
                    +-----------------------+
                    |  Verification Engine  |   re-observe, assert, PASS/FAIL
                    +-----------------------+

  Cross-cutting: Observability | Audit Trail | Evaluation | Security | Model Serving
                 Task Queue    | Persistence | Policy      | Secrets
```

---

## 4. What Vyomel is NOT (explicit non-goals)

Writing these down prevents scope drift. Each is a **deliberate** exclusion, not an oversight.

| Non-goal | Reason |
|---|---|
| A general-purpose chatbot UI | Text answers are a side effect, not the product. |
| A multi-user SaaS with tenancy/billing | Single-user, self-hosted. Multi-tenancy is a distraction from the hard parts. |
| Autonomous financial transactions | Permanently gated at capability L4 with mandatory human approval. Never auto-approved. |
| Training or fine-tuning foundation models | Vyomel is an *inference and orchestration* system. Model training is out of scope. |
| Mobile app (v1) | Interface-agnostic core is designed for it; the app itself is post-v1. |
| Smart glasses hardware | Vyomel targets the *software* layer. Glasses = another client of the same API. |
| Beating a benchmark leaderboard | We build our own reproducible evaluation suite instead. |

---

## 5. Differentiation thesis

"My AI controls the computer" is not a differentiator — OpenAI, Rabbit, and several startups already ship that. Vyomel's defensible combination is:

1. **Persistent personal context graph** — entities and relationships, not a pile of embeddings. "Continue Orbit from yesterday" resolves to real state.
2. **Capability-based permission model** — a formal L0–L4 lattice enforced in the runtime, not prompt-level "please ask first."
3. **Deterministic post-action verification** — the agent must *re-observe* and assert the outcome. No action is `SUCCEEDED` on the model's word alone.
4. **Durable, resumable execution** — tasks survive process crashes, machine restarts, and hour-long runtimes. Postgres is the source of truth; Redis is only transport.
5. **Local/cloud privacy routing** — model selection driven by data sensitivity, not just cost. Credential-bearing screens never leave the machine.
6. **Workflow learning** — recurring action sequences are mined from the audit trail and promoted into reusable, parameterized workflows.
7. **Interface independence** — desktop, browser, voice, wearable are all clients of one API. The intelligence is not bound to a UI.

An honest read: items 1, 3, 4, and 5 are where the real engineering lives and where this project is meaningfully differentiated at a technical-interview level.

---

## 6. Target scenarios (the north-star tasks)

These drive the evaluation suite. They are ordered by increasing difficulty.

| # | Scenario | Capabilities exercised |
|---|---|---|
| S1 | "What did I decide about the Orbit retry policy?" | RAG, context graph |
| S2 | "Summarize the three PDFs in `~/Downloads` from this week and file them under Orbit." | file tools, L1 writes |
| S3 | "Find yesterday's interview email, check my calendar, propose two prep blocks, create them." | API tools, planning, L2/L3 gates |
| S4 | "Adapt my latest resume for this backend role and prepare the application, pause before submit." | RAG, document edit, browser, human-in-loop |
| S5 | "Grade this submission against the CS151 rubric and enter it once I approve." | vision/screen, planner, verification |
| S6 | "Research 100 companies, rank roles relevant to me, produce a report." | long-running durable execution, parallel DAG |
| S7 | "Take these 12 clips, cut a 60-second draft, remove profanity, add captions." | media agent, FFmpeg pipeline |
| S8 | "I'm at the gym — look at the equipment and build today's session." | camera perception, personal history |

S1–S6 are in scope for v1. S7 and S8 are v2 verticals that must be built as **plugins on the same runtime**, not separate products.

---

## 7. Guiding engineering principles

1. **Deterministic scaffolding around probabilistic cores.** The LLM proposes; the runtime disposes. State machines, schemas, and policies are code, not prompts.
2. **Structured interfaces before pixels.** Control hierarchy: `Native API > Accessibility tree > DOM > Vision/coordinates`. Vision is the fallback, never the default.
3. **Everything is observable.** If a task ran, there is a trace, a span per action, and an audit record. No silent work.
4. **Fail closed.** Unknown permission level ⇒ deny. Unverifiable action ⇒ not `SUCCEEDED`. Missing policy ⇒ escalate to human.
5. **Bounded autonomy.** Every loop has a cap: `max_replans`, `max_retries`, `max_steps`, `max_wall_clock`, `max_token_budget`.
6. **Measure before claiming.** No performance or accuracy statement enters the README or the resume without a reproducible benchmark script in `evals/`.
7. **Build vertically, not horizontally.** Each milestone ships an end-to-end usable slice, not a layer.

---

## 8. Document map

| Doc | Purpose |
|---|---|
| `00-OVERVIEW.md` | This document. Vision, scope, non-goals. |
| `01-REQUIREMENTS.md` | Numbered functional (FR) and non-functional (NFR) requirements. |
| `02-ARCHITECTURE.md` | Components, boundaries, data flow, deployment topology. |
| `03-DATA-MODEL.md` | Postgres schema, entities, pgvector layout, migrations. |
| `04-API-SPEC.md` | HTTP/WebSocket contract. |
| `05-TOOL-SPEC.md` | Tool interface contract and the full tool catalog. |
| `06-SECURITY-PERMISSIONS.md` | Capability lattice, policy engine, secrets, audit, threat model. |
| `07-EXECUTION-ENGINE.md` | Task DAG, action state machine, durability, queueing, replanning. |
| `08-MEMORY-RAG.md` | Context graph, ingestion, chunking, hybrid retrieval, evaluation. |
| `09-MODEL-SERVING.md` | Provider abstraction, model router, vLLM plan, benchmarking. |
| `10-OBSERVABILITY.md` | Tracing, metrics, logs, dashboards, SLOs. |
| `11-EVALUATION.md` | Benchmark suites, metric definitions, harness design. |
| `12-ROADMAP.md` | Milestones M0–M15 with entry/exit criteria. |
| `13-ENVIRONMENT.md` | Verified machine facts, constraints, setup runbook. |
| `14-RESUME-MAPPING.md` | Every resume claim → the artifact that proves it. |
| `15-RISKS.md` | Risk register and mitigations. |
| `16-WORKFLOW.md` | Dev workflow, branch/commit policy, definition of done. |
| `adr/` | Architecture Decision Records. |
