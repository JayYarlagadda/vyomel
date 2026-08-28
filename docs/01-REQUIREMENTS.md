# 01 — Requirements

Status: **Approved baseline (v1.0)**

Every requirement has a stable ID. IDs are never reused or renumbered. Tests reference these IDs; the traceability matrix in §5 must stay complete.

Priority levels:
- **P0** — v1 cannot ship without it. Also: required to make a resume claim true.
- **P1** — v1 target, degrade gracefully if missing.
- **P2** — post-v1.

---

## 1. Functional requirements

### 1.1 Intent & Planning (FR-1xx)

| ID | Requirement | Priority | Verified by |
|---|---|---|---|
| FR-101 | Accept a natural-language instruction via HTTP, CLI, or WebSocket and create a persisted `Task`. | P0 | `tests/api/test_tasks.py` |
| FR-102 | Decompose an instruction into a **directed acyclic graph** of `Step`s with explicit dependencies, not a linear chain. | P0 | `tests/planner/test_dag.py` |
| FR-103 | Produce plans as **schema-validated structured output**; a plan that fails schema validation is rejected and re-requested (max 2 retries) rather than parsed heuristically. | P0 | `tests/planner/test_schema.py` |
| FR-104 | Select tools only from the capability-filtered catalog available to the requesting principal. | P0 | `tests/planner/test_tool_scoping.py` |
| FR-105 | Attach to each step: preconditions, expected postconditions, required capability level, retry policy, and timeout. | P0 | `tests/planner/test_step_contract.py` |
| FR-106 | Replan when a step fails or a postcondition is unmet, bounded by `max_replans` (default 3), after which the task enters `NEEDS_HUMAN`. | P0 | `tests/planner/test_replan_bounds.py` |
| FR-107 | Support explicit user-supplied plans (`plan_override`) that bypass the planner, for deterministic workflows. | P1 | `tests/planner/test_override.py` |
| FR-108 | Estimate cost and token budget for a plan before execution and refuse plans exceeding `max_token_budget`. | P1 | `tests/planner/test_budget.py` |

### 1.2 Execution Runtime (FR-2xx)

| ID | Requirement | Priority | Verified by |
|---|---|---|---|
| FR-201 | Persist every task, step, and action to Postgres **before** dispatch. Postgres is the single source of truth. | P0 | `tests/runtime/test_persistence.py` |
| FR-202 | Survive worker crash and full process restart with **no lost and no duplicated** side-effecting actions. | P0 | `tests/runtime/test_crash_recovery.py` |
| FR-203 | Implement the action state machine exactly as specified in `07-EXECUTION-ENGINE.md` §3, rejecting illegal transitions. | P0 | `tests/runtime/test_state_machine.py` |
| FR-204 | Execute independent DAG branches concurrently, bounded by `max_parallel_actions` (default 4). | P0 | `tests/runtime/test_parallel.py` |
| FR-205 | Enforce per-action `timeout_s` and per-task `max_wall_clock_s`. | P0 | `tests/runtime/test_timeouts.py` |
| FR-206 | Retry retryable failures with exponential backoff + jitter, capped by `max_retries` (default 2). | P0 | `tests/runtime/test_retry.py` |
| FR-207 | Guarantee idempotency for side-effecting tools via a caller-supplied `idempotency_key`; a replayed action must not duplicate the effect. | P0 | `tests/runtime/test_idempotency.py` |
| FR-208 | Support tasks that run for **≥ 30 minutes** without a client connection, resumable after restart. | P0 | `evals/suites/longrun/` |
| FR-209 | Support pause, resume, and cancel on a running task; cancel must attempt compensation for reversible completed actions. | P1 | `tests/runtime/test_lifecycle.py` |
| FR-210 | Reclaim actions abandoned by dead workers via a lease/visibility-timeout reaper. | P0 | `tests/runtime/test_reaper.py` |

### 1.3 Permissions & Human-in-the-loop (FR-3xx)

| ID | Requirement | Priority | Verified by |
|---|---|---|---|
| FR-301 | Classify every tool invocation into capability level **L0–L4** (`06-SECURITY-PERMISSIONS.md` §2). | P0 | `tests/security/test_classification.py` |
| FR-302 | Evaluate a declarative policy before every action; **default deny** on any unmatched or ambiguous case. | P0 | `tests/security/test_policy_default_deny.py` |
| FR-303 | Block the action in `WAITING_FOR_USER` and emit an approval request when policy requires confirmation. | P0 | `tests/security/test_approval_gate.py` |
| FR-304 | Present approval requests with: intent, exact tool + parameters, capability level, reversibility, and blast radius. | P0 | `tests/api/test_approvals.py` |
| FR-305 | Expire unanswered approvals after `approval_ttl_s` (default 3600) and fail the action closed. | P0 | `tests/security/test_approval_expiry.py` |
| FR-306 | Never auto-approve **L4**. No configuration flag may disable this. | P0 | `tests/security/test_l4_never_auto.py` |
| FR-307 | Write an immutable, append-only audit record for every action, decision, and approval. | P0 | `tests/security/test_audit_append_only.py` |
| FR-308 | Redact secrets from all logs, traces, prompts, and audit payloads using a central redaction filter. | P0 | `tests/security/test_redaction.py` |
| FR-309 | Support per-tool, per-application, and per-domain permission scoping. | P1 | `tests/security/test_scopes.py` |
| FR-310 | Support "trust this workflow" — promoting a verified recurring workflow to reduced friction, capped at L2. | P2 | `tests/security/test_trusted_workflows.py` |

### 1.4 Verification (FR-4xx)

| ID | Requirement | Priority | Verified by |
|---|---|---|---|
| FR-401 | After every consequential (≥ L2) action, **re-observe** the environment and assert declared postconditions. | P0 | `tests/verify/test_postconditions.py` |
| FR-402 | Mark an action `SUCCEEDED` only when verification passes; unverifiable ⇒ `UNVERIFIED`, never `SUCCEEDED`. | P0 | `tests/verify/test_no_assumed_success.py` |
| FR-403 | Support verifier types: `value_equals`, `element_exists`, `file_exists`, `file_hash`, `api_readback`, `llm_judge`. | P0 | `tests/verify/test_verifier_types.py` |
| FR-404 | Verification must use a **different observation path** than the action where possible (e.g. write via API, read back via API + UI). | P1 | `tests/verify/test_independent_path.py` |
| FR-405 | Record every verification outcome with evidence (value read, screenshot hash, API response) in the audit trail. | P0 | `tests/verify/test_evidence.py` |

### 1.5 Memory & Retrieval (FR-5xx)

| ID | Requirement | Priority | Verified by |
|---|---|---|---|
| FR-501 | Ingest local documents (`pdf, docx, md, txt, html, csv, code`) into chunked, embedded, indexed form. | P0 | `tests/memory/test_ingest.py` |
| FR-502 | Maintain a **personal context graph** of typed entities (person, project, document, application, task, preference, workflow) and typed relationships. | P0 | `tests/memory/test_graph.py` |
| FR-503 | Hybrid retrieval: BM25/full-text + vector similarity, fused with Reciprocal Rank Fusion. | P0 | `tests/memory/test_hybrid.py` |
| FR-504 | Structured state lives in relational tables; **only** semantic content is embedded. No "everything in the vector DB." | P0 | design review + `03-DATA-MODEL.md` |
| FR-505 | Every retrieved chunk carries a citation (source path, page/offset, ingestion timestamp). | P0 | `tests/memory/test_citations.py` |
| FR-506 | Incremental re-ingestion on file change, detected by content hash. | P1 | `tests/memory/test_incremental.py` |
| FR-507 | Record episodic memory: what the agent did, when, for which project, with what outcome. | P0 | `tests/memory/test_episodic.py` |
| FR-508 | Resolve references like "yesterday", "the Orbit repo", "that email" against the context graph. | P1 | `tests/memory/test_reference_resolution.py` |
| FR-509 | Support `forget` — hard delete of an entity, its chunks, embeddings, and derived episodic records. | P0 | `tests/memory/test_forget.py` |

### 1.6 Tools (FR-6xx)

| ID | Requirement | Priority | Verified by |
|---|---|---|---|
| FR-601 | Every tool declares a JSON-Schema input model, an output model, a capability level, reversibility, and idempotency semantics. | P0 | `tests/tools/test_contract.py` |
| FR-602 | Tools are discovered via a registry and exposed to the model as structured function definitions. | P0 | `tests/tools/test_registry.py` |
| FR-603 | Filesystem tools operate only inside configured allowlisted roots; path traversal is rejected. | P0 | `tests/tools/test_fs_sandbox.py` |
| FR-604 | Browser tools drive a real browser (Playwright/CDP) using the accessibility tree and DOM selectors before coordinates. | P0 | `tests/tools/test_browser.py` |
| FR-605 | Desktop tools use Windows UI Automation before screen coordinates; the fallback hierarchy is enforced in code. | P0 | `tests/tools/test_desktop_hierarchy.py` |
| FR-606 | API tools (Gmail, Calendar, GitHub) use OAuth with least-privilege scopes and refresh-token rotation. | P1 | `tests/tools/test_oauth.py` |
| FR-607 | Media tools (transcribe, cut, mute, caption) operate as an FFmpeg-backed plugin on the same runtime. | P2 | `tests/tools/test_media.py` |
| FR-608 | A failing tool returns a **structured** error (`code`, `retryable`, `message`, `observation`), never a raw exception string to the planner. | P0 | `tests/tools/test_error_contract.py` |

### 1.7 Model serving (FR-7xx)

| ID | Requirement | Priority | Verified by |
|---|---|---|---|
| FR-701 | Uniform `ModelProvider` interface over OpenAI-compatible, Anthropic, vLLM, and llama.cpp/Ollama backends. | P0 | `tests/models/test_providers.py` |
| FR-702 | A **model router** selects a backend from: data sensitivity, task complexity, context size, latency target, and cost ceiling. | P0 | `tests/models/test_router.py` |
| FR-703 | Data classified `SENSITIVE` (credentials, financial, health, secret-bearing screens) must **never** be sent to a cloud provider. | P0 | `tests/models/test_privacy_routing.py` |
| FR-704 | Record per-call: model, prompt/completion tokens, TTFT, total latency, and cost. | P0 | `tests/models/test_accounting.py` |
| FR-705 | Fail over to a secondary provider on 5xx/timeout, with circuit breaking on repeated failure. | P1 | `tests/models/test_failover.py` |
| FR-706 | Deterministic mode: fixed seed + temperature 0 + response caching, for reproducible evaluation. | P0 | `tests/models/test_determinism.py` |
| FR-707 | Self-hosted vLLM deployment (Docker + K8s manifests) with reproducible throughput benchmarks. | P0 | `evals/suites/serving/` |

### 1.8 Observability (FR-8xx)

| ID | Requirement | Priority | Verified by |
|---|---|---|---|
| FR-801 | Emit OpenTelemetry traces with one span per task, step, action, model call, and retrieval. | P0 | `tests/obs/test_spans.py` |
| FR-802 | Expose Prometheus metrics per `10-OBSERVABILITY.md` §3. | P0 | `tests/obs/test_metrics.py` |
| FR-803 | Structured JSON logs with `task_id`, `step_id`, `action_id`, and `trace_id` correlation on every record. | P0 | `tests/obs/test_log_correlation.py` |
| FR-804 | Per-task trace timeline retrievable via API and rendered in the CLI. | P1 | `tests/api/test_trace_view.py` |
| FR-805 | Ship Grafana dashboards as versioned JSON in `infra/grafana/`. | P1 | manual + `helm template` |

### 1.9 Workflow learning (FR-9xx)

| ID | Requirement | Priority | Verified by |
|---|---|---|---|
| FR-901 | Mine the audit trail for recurring action sequences (frequent-sequence detection over normalized action signatures). | P2 | `tests/learning/test_mining.py` |
| FR-902 | Propose a parameterized reusable workflow when a sequence recurs ≥ N times (default 3). | P2 | `tests/learning/test_proposal.py` |
| FR-903 | Learned workflows require explicit user acceptance before becoming invocable. | P2 | `tests/learning/test_acceptance.py` |

---

## 2. Non-functional requirements

| ID | Requirement | Target | Verified by |
|---|---|---|---|
| NFR-01 | API p95 latency for non-LLM endpoints | < 100 ms | `evals/suites/api_latency/` |
| NFR-02 | Task creation → first action dispatched | p95 < 3 s | `evals/suites/dispatch/` |
| NFR-03 | Durability: side-effecting actions lost or duplicated after crash | **0** | `tests/runtime/test_crash_recovery.py` |
| NFR-04 | Retrieval recall@10 on the personal-docs benchmark | ≥ 0.85 | `evals/suites/rag/` |
| NFR-05 | Tool-call schema validity rate | ≥ 0.98 | `evals/suites/agent/` |
| NFR-06 | Verification coverage on ≥ L2 actions | 100 % | `tests/verify/test_coverage.py` |
| NFR-07 | Unit + integration test line coverage on `astra/core`, `astra/runtime`, `astra/security` | ≥ 85 % | `pytest --cov` gate in CI |
| NFR-08 | Cold start of API process | < 5 s | `evals/suites/api_latency/` |
| NFR-09 | Secret leakage into logs/traces/prompts | **0 occurrences** | `tests/security/test_redaction.py` + CI secret scan |
| NFR-10 | Every P0 requirement has at least one automated test | 100 % | `scripts/check_traceability.py` in CI |
| NFR-11 | Reproducible evaluation: same seed + same fixtures ⇒ identical scores | bit-identical | `evals/harness/test_reproducibility.py` |
| NFR-12 | Local-model path works fully offline (no network) | pass | `tests/models/test_offline.py` |

---

## 3. Constraints

| ID | Constraint | Source |
|---|---|---|
| CON-01 | Python 3.13.5 for all application code. | `13-ENVIRONMENT.md` C-4 |
| CON-02 | Postgres 17 + pgvector and Redis 7 run in WSL Docker; app runs on Windows. | C-3 |
| CON-03 | No local vLLM; benchmarks on rented GPU. | C-1 |
| CON-04 | Local models limited to quantized ≤ 8B GGUF. | C-2 |
| CON-05 | Desktop control targets Windows UIA first; macOS backend is interface-compatible future work. | C-5 |
| CON-06 | Single-user system. No multi-tenancy. | `00-OVERVIEW.md` §4 |
| CON-07 | Kubernetes validated via `kind`/short-lived cloud cluster, not run continuously locally. | C-7 |

---

## 4. Assumptions

| ID | Assumption | If false |
|---|---|---|
| ASM-01 | The user is the sole principal and physically trusts the machine. | Multi-principal auth becomes P0. |
| ASM-02 | Hosted LLM API access (at least one of OpenAI/Anthropic/Google) is available. | System still works local-only, with reduced planning quality (NFR-12 guarantees this path). |
| ASM-03 | Target desktop apps expose a usable UI Automation tree. | Vision fallback rate rises; this is measured explicitly as `desktop_fallback_to_vision_ratio`. |
| ASM-04 | A rented GPU is available for a handful of hours for serving benchmarks. | The vLLM claim must be softened to "designed/deployed" without throughput numbers. |

---

## 5. Traceability

`scripts/check_traceability.py` parses this file for all `FR-*`/`NFR-*` IDs, greps the test tree for `@pytest.mark.req("FR-xxx")` markers, and fails CI if any **P0** requirement has no linked test. This is the mechanism that keeps documentation and reality in sync — it is the single most important process control in this project.
