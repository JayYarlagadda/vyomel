# 14 — Resume Claim Traceability

Status: **Living document — update at every milestone**

The resume already lists Vyomel. That inverts the usual order: instead of building something and then describing it, there is a written claim that must be made true. This document is the contract between the two.

**Rule: at any moment, every claim on the resume must be either (a) implemented and tested, or (b) removed from the resume.** There is no third state.

---

## 1. Current resume text

> **Vyomel – Personal AI Execution Platform** | Python, FastAPI, LLMs, RAG, pgvector, Redis, vLLM, Kubernetes
>
> • Built Vyomel, a persistent AI execution platform that converts natural-language requests into permission-aware, multi-step workflows across browser, desktop, and API tools using RAG-based personal context, durable asynchronous execution, and structured tool calling.
>
> • Designed a self-hosted AI infrastructure layer with vLLM, vector retrieval, Redis-backed task queues, persistent workflow state, post-action verification, and bounded replanning to support long-running autonomous tasks while measuring task completion, tool-call accuracy, retrieval quality, latency, and human-intervention rate.

---

## 2. Claim decomposition and evidence

| # | Claim phrase | Proven by | Milestone | Status |
|---|---|---|---|---|
| C1 | "persistent … platform" | Postgres-as-truth; `tests/runtime/test_persistence.py`; restart survives | M0–M1 | ◐ tasks, steps, and actions persist; worker crash-replay in `test_crash_recovery.py` |
| C2 | "converts natural-language requests into … multi-step workflows" | `vyomel/planner/`; `tests/planner/test_decompose.py` (list→report DAG); `evals/suites/agent/` multi-step fixtures + `multi_step_accuracy` | M5 | ☑ mock planner emits dependent multi-step plans; agent eval reports `multi_step_accuracy` |
| C3 | "permission-aware" | `vyomel/security/`; capability lattice; `tests/security/` | M2 | ☑ classification with escalation, default-deny policy, L4 invariant under Hypothesis fuzzing, single-use approvals bound to parameters and level, hash-chained append-only audit |
| C4 | "across browser … tools" | `vyomel/tools/browser/`; `evals/suites/browser/` | M7 | ☑ 40 fixture workflows, 1.0 success_rate, a11y-first resolver |
| C5 | "… desktop …" | `vyomel/tools/desktop/`; `evals/suites/desktop/` | M8 | ☑ 50 fixture workflows, 1.0 success_rate, UIA-first resolver |
| C6 | "… and API tools" | `vyomel/tools/api/`; `evals/suites/api/` | M9 | ☑ S3 fixture: interview email → free slots → 2 L3 event creates, both CONFIRM |
| C7 | "RAG-based personal context" | `vyomel/memory/`; hybrid retrieval; `evals/suites/rag/` | M4 | ◐ md/txt ingest, hashing embedder, hybrid RRF + citations; bge/evals still open |
| C8 | "durable asynchronous execution" | Redis Streams + leases + reaper; `evals/suites/longrun/` chaos results | M1, M6 | ◐ streams, leases, reaper green; chaos suite is M6 |
| C9 | "structured tool calling" | Pydantic → JSON-Schema tool defs; `schema_validity_rate` in `evals/suites/agent/` | M5 | ☑ plan + per-tool Input validation; agent eval gates `schema_validity_rate` ≥ 0.98 |
| C10 | "self-hosted AI infrastructure layer with vLLM" | `vyomel/models/providers/vllm.py`; `infra/vllm/`; `infra/k8s/vllm-statefulset.yaml`; `evals/results/serving/` | M11 | ◐ adapter + manifests + fixture throughput table committed; live A10G numbers pending rented session (`infra/vllm/up.ps1`) |
| C11 | "vector retrieval" (pgvector) | HNSW index on `document_chunks.embedding` | M4 | ◐ HNSW + cosine query green; production model is still the hashing stand-in |
| C12 | "Redis-backed task queues" | `vyomel/runtime/queue.py` — Streams, consumer groups, `XAUTOCLAIM` | M1 | ☑ claim/ack/recovery covered by DAG, timeout, and crash-replay tests; `demos/m1/` shows it |
| C13 | "persistent workflow state" | `tasks`/`steps`/`actions` + state machine + startup recovery | M1 | ☑ schema, state machine locked to `07` §3, recovery republish of orphan DISPATCHED |
| C14 | "post-action verification" | `vyomel/verify/`; `verification_catch_rate` = 100 % on injected faults | M3 | ☑ `value_equals`/`file_exists`/`file_hash`/`element_exists`/`api_readback` re-observe; lying writes fail in `tests/verify/test_catch_rate.py`; browser/API tools declare the new verifiers; `llm_judge` remains `NO_METHOD` until a dedicated judge model path exists |
| C15 | "bounded replanning" | `max_replans` with hard ceiling; `tests/planner/test_replan_bounds.py` | M5 | ☑ recovery succeeds under budget; `max_replans=0` → `NEEDS_HUMAN`; config ceiling rejects >5 |
| C16 | "long-running autonomous tasks" | 30-min task, no client connection, survives restart | M6 | ◐ standard 600s kill-replay eval committed (`evals/results/2026-09-02-m6/`); restart + Redis flush survival in `tests/runtime/test_longrun.py`; full 20-min chaos / wall-clock 30-min still manual |
| C17 | "measuring task completion, tool-call accuracy, retrieval quality, latency, human-intervention rate" | `evals/` harness; all five metrics in `evals/results/` | M4, M5, M12 | ◐ completion, tool-call, recall@10, serving latency committed; human-intervention rate still sparse outside agent adversarial subset; CI `eval-gate` live |
| C18 | "Kubernetes" (skills line) | `infra/helm/vyomel/` + `infra/k8s/vllm-statefulset.yaml`; kind CI | M13 | ◐ Helm chart + kind CI smoke; short-lived cloud cluster apply still a manual rental session |
| C19 | "FastAPI" | `vyomel/api/` — health, readiness, tasks; 46 tests green | M0 | ☑ |
| C20 | "Python" | entire codebase, `mypy --strict` clean | M0 | ☑ |
| C21 | Media / S7 plugin on same runtime | `vyomel/tools/media/`; `evals/suites/media/`; FR-607 | M14 | ☑ fixture S7: 12 clips → 60s draft, profanity mute, captions, export CONFIRM |

Legend: ☐ not started · ◐ partial · ☑ complete and green in CI.

**Update discipline:** flip a box to ☑ only when the linked test or eval result is committed and green in CI. `scripts/check_resume_claims.py` re-verifies each linked artifact exists and its tests pass, so this table cannot silently drift.

---

## 3. Claim-safety review

Where a claim could be challenged in an interview, the honest answer is written down now.

| Claim | Risk | Honest position |
|---|---|---|
| vLLM | "You have a 2 GB MX330 — where did you run vLLM?" | "Locally I run llama.cpp; vLLM runs on a rented A10G. Here are the deployment manifests, the provider adapter, and the throughput-vs-concurrency table with reproduction commands." That is a stronger answer than a vague one. |
| Kubernetes | "Is it actually deployed?" | "Helm chart validated on `kind` in CI and deployed once to a short-lived cloud cluster. Desktop tools can't run in a pod, so there's a local-agent split — here's the ADR explaining why." |
| "durable" | "How do you know?" | Chaos test results: 20 minutes of random `kill -9`, zero lost or duplicated effects. Show the write-ordering argument (Postgres commit before Redis publish). |
| "permission-aware" | "Isn't that just a prompt saying 'ask first'?" | No — a capability lattice enforced in the runtime, with policy fuzzing tests proving L4 can never be auto-approved. |
| "measuring …" | "What are the numbers?" | Committed eval results with the exact model versions, seeds, and fixture hashes. |
| "post-action verification" | "Doesn't the model just claim success?" | The `UNVERIFIED` state exists specifically so it can't. Fault-injection tests prove wrong values are caught 100 % of the time. |

The pattern: **every risky claim is answered by an artifact, not by a story.**

---

## 4. Numbers to replace generic language

As results land, these placeholders get real values:

| Placeholder | Source |
|---|---|
| "task completion rate of **N %** across 100 multi-step benchmark tasks" | `evals/suites/agent/` |
| "**N %** tool-call accuracy" | `evals/suites/agent/` |
| "recall@10 of **N** on a 200-document personal corpus" | `evals/suites/rag/` |
| "**N ×** throughput improvement over an unbatched baseline at concurrency 16" | `evals/suites/serving/` |
| "reduced cost per task **N %** by routing extraction/classification to a local 7B model" | `model_calls` analysis |
| "zero lost or duplicated actions across **N** induced worker crashes" | `evals/suites/longrun/` |
| "**N %** of desktop actions resolved via UI Automation rather than coordinates" | `vyomel_actuation_tier_total` |

---

## 5. Stretch value beyond the resume bullet

Things this project can produce that most candidate projects cannot. Each is cheap given the work is already being done.

| Artifact | Why it is worth more than another feature |
|---|---|
| **Public eval results with ablations** | Turns "I built an agent" into "I measured agents." Very few candidates have this. |
| **A written prompt-injection findings report** | Security-relevant, publishable, and directly adjacent to the author's Microsoft Security Platform & AI internship. |
| **vLLM vs baseline serving benchmark writeup** | Concrete inference-infrastructure evidence; pairs naturally with the cuDF and Harmony performance PRs already on the resume. |
| **The durable DAG executor as a standalone open-source library** | The most reusable piece. A small, well-tested `pg + redis` durable task engine is genuinely useful to others and is a strong portfolio object on its own. |
| **A technical blog series** (durable execution, capability models for agents, hybrid retrieval on personal corpora, verification) | Converts work already done into visible signal; each post is a rewritten doc from this folder. |
| **OpenTelemetry semantic conventions for agent systems** | The author already has OTel production experience. Proposing agent-specific conventions upstream is a distinctive, low-cost contribution. |
| **Conference/meetup talk: "Agents are a distributed systems problem"** | The project's actual thesis, and a differentiated angle in a crowded space. |

The single highest-leverage extra: **extract the durable execution engine into its own package.** It is self-contained, broadly useful, easy to test, and demonstrates exactly the distributed-systems judgment the target roles hire for.
