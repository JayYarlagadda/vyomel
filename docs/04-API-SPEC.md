# 04 — API Specification

Status: **Approved baseline (v1.0)**
Base URL: `http://localhost:8080`
Versioning: path-based (`/v1/...`). Breaking changes require `/v2`.

---

## 1. Conventions

| Concern | Convention |
|---|---|
| IDs | ULID strings |
| Timestamps | RFC 3339 UTC (`2026-08-28T07:12:03.441Z`) |
| Errors | RFC 9457 Problem Details + an Astra `code` |
| Auth | Local bearer token from `.env` (`ASTRA_API_TOKEN`); loopback-only bind by default |
| Idempotency | `Idempotency-Key` header on all `POST` |
| Pagination | Cursor-based: `?cursor=&limit=` → `{items, next_cursor}` |
| Streaming | WebSocket for task events; SSE for token streaming |
| Correlation | Every response carries `X-Trace-Id` |

### Error shape

```json
{
  "type": "https://astra.dev/errors/permission-denied",
  "title": "Permission denied",
  "status": 403,
  "code": "PERMISSION_DENIED",
  "detail": "Tool 'fs.delete' on '**/.ssh/**' is denied by rule 'protected-paths'.",
  "retryable": false,
  "task_id": "01J8X...",
  "trace_id": "4bf92f..."
}
```

---

## 2. Tasks

### `POST /v1/tasks`

Create and (optionally) start a task.

```json
{
  "instruction": "Find yesterday's interview email, check my calendar, propose two prep blocks",
  "context_hints": {
    "active_app": "chrome",
    "selection": null,
    "paths": ["D:/Documents/JobSearch"]
  },
  "capability_ceiling": "L3",
  "bounds": { "max_wall_clock_s": 1800, "max_cost_usd": 1.0, "max_replans": 3 },
  "autostart": true,
  "dry_run": false
}
```

`201` → the task object. `capability_ceiling` is the user's up-front consent boundary; nothing discovered during execution can raise it (`06-SECURITY-PERMISSIONS.md` §5.2).

`dry_run: true` plans without executing — useful for inspecting what Astra *would* do.

### `GET /v1/tasks/{id}`

```json
{
  "id": "01J8X...",
  "instruction": "...",
  "status": "WAITING_FOR_USER",
  "plan_version": 1,
  "replan_count": 0,
  "progress": { "steps_total": 6, "steps_done": 3, "actions_total": 9, "actions_done": 5 },
  "pending_approval_id": "01J8Y...",
  "tokens_used": 14203,
  "cost_usd": 0.0412,
  "trace_id": "4bf92f...",
  "created_at": "...", "started_at": "...", "finished_at": null
}
```

### Other task endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/tasks` | List, filterable by `status`, `origin`, `since` |
| `GET` | `/v1/tasks/{id}/plan` | Current plan: steps, edges, tools, capability levels |
| `GET` | `/v1/tasks/{id}/actions` | All actions with status, result summary, verification outcome |
| `GET` | `/v1/tasks/{id}/trace` | Trace timeline (FR-804) |
| `GET` | `/v1/tasks/{id}/audit` | Audit records for this task |
| `POST` | `/v1/tasks/{id}/start` | Start a task created with `autostart:false` |
| `POST` | `/v1/tasks/{id}/pause` | → `PAUSED` after in-flight actions settle |
| `POST` | `/v1/tasks/{id}/resume` | |
| `POST` | `/v1/tasks/{id}/cancel` | `{compensate: true}` — returns which effects could **not** be undone |
| `POST` | `/v1/tasks/{id}/reply` | Answer a `NEEDS_HUMAN` clarification and resume |

### `WS /v1/tasks/{id}/events`

Event stream. Event types: `task.status_changed`, `plan.created`, `plan.revised`, `step.started`, `step.finished`, `action.dispatched`, `action.started`, `action.finished`, `approval.requested`, `approval.decided`, `verification.completed`, `token` (streamed model output), `task.finished`.

```json
{ "event": "action.finished", "ts": "...", "data": {
    "action_id": "01J8Z...", "tool": "desktop.set_field",
    "status": "SUCCEEDED", "verification": "PASS", "duration_ms": 1512 } }
```

---

## 3. Approvals

### `GET /v1/approvals?status=PENDING`

```json
{ "items": [{
    "id": "01J8Y...",
    "task_id": "01J8X...",
    "action_id": "01J8Z...",
    "capability_level": "L3",
    "summary": "Enter grade 87 for Student 482 in Canvas Gradebook",
    "tool": "desktop.set_field",
    "parameters": { "window": "Canvas — Gradebook", "element": "Grade[482]", "value": 87 },
    "blast_radius": { "affects": ["gradebook cell (1)"], "reversible": true,
                      "externally_visible": true, "previous_value": null },
    "verification_plan": [{ "type": "value_equals", "expected": 87, "tier": 2 }],
    "reasoning": "Correctness 38/40, Design 24/30, Testing 15/20, Docs 10/10",
    "citations": [{ "source": "CS151_Rubric_v3.pdf", "page": 2 }],
    "expires_at": "..."
  }] }
```

### `POST /v1/approvals/{id}/decide`

```json
{ "decision": "APPROVED" }
{ "decision": "MODIFIED", "parameters": { "value": 85 } }
{ "decision": "REJECTED", "reason": "Wrong student" }
```

`MODIFIED` re-validates against the tool schema and **re-classifies** capability. If the modification raises the level, a new approval is required — a modification cannot be used to slip past a gate (`06` §4.2).

Responses are the approval object. Failure modes, all of which are reachable in normal use rather than exceptional:

| Status | When |
|---|---|
| `404` | No such approval |
| `409` | Already decided, expired, or the action has moved on (cancelled, or failed closed by the expiry sweeper) |
| `409` | A `MODIFIED` edit fails the tool's input schema, or classifies above the task's capability ceiling |
| `422` | `MODIFIED` without `parameters`, or `parameters` sent with any other decision |

An approval is bound to `(action_id, parameter_hash, capability_level)` and is single-use. Editing the action after approval, or a re-classification that raises its level, voids it: the gate finds no usable approval and asks again.

---

## 4. Memory

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/memory/ingest` | `{paths[], recursive, watch}` → ingestion job |
| `GET` | `/v1/memory/ingest/{job_id}` | Progress |
| `POST` | `/v1/memory/query` | `{query, k, strategy, filters}` → chunks + citations + scores |
| `GET` | `/v1/memory/entities` | Filter by `type`, `q` |
| `GET` | `/v1/memory/entities/{id}` | Entity + relations + linked documents + recent episodes |
| `POST` | `/v1/memory/remember` | Assert a fact/preference |
| `DELETE` | `/v1/memory/entities/{id}` | Hard delete, cascades to chunks/embeddings/episodes (FR-509) |
| `GET` | `/v1/memory/episodes` | Filter by `entity_id`, `since` |

Query response always includes citations:

```json
{ "results": [{
    "chunk_id": "01J9...", "content": "...",
    "score": 0.83, "vector_rank": 2, "lexical_rank": 1,
    "citation": { "path": "D:/Docs/orbit/architecture.md",
                  "heading_path": ["Design","Retry Policy"],
                  "char_start": 4102, "char_end": 4640,
                  "ingested_at": "..." } }],
  "strategy": "hybrid_rrf", "latency_ms": 187 }
```

`POST /v1/memory/ingest` is synchronous in the current slice (md/txt only; `watch` returns 501). `GET /v1/memory/ingest/{job_id}` and entity/episode routes wait for later M4 work.

---

## 5. Tools, workflows, admin

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/tools` | Catalog with schemas, capability levels, tiers |
| `GET` | `/v1/tools/{name}` | Full definition |
| `POST` | `/v1/tools/{name}/invoke` | Direct invocation (debug); still fully policy-gated and audited. `CONFIRM` and `DENY` fail closed — this path does not collect consent |
| `GET` | `/v1/workflows` | Saved + learned workflows |
| `POST` | `/v1/workflows/{id}/accept` | Accept a learned workflow proposal |
| `POST` | `/v1/workflows/{id}/invoke` | Run with parameters |
| `GET` | `/v1/policy` | Active policy + version hash |
| `POST` | `/v1/policy/reload` | Hot reload from disk |
| `POST` | `/v1/policy/test` | `{tool, parameters}` → classification, escalation reasons, decision, deciding rule. Does not execute anything |
| `GET` | `/v1/audit` | Filter by `task_id`, `action_id`, `event_type`, `since` |
| `POST` | `/v1/audit/verify` | Walk the hash chain; report first divergence. `?start_id=` verifies a suffix |
| `GET` | `/v1/models` | Providers, health, routing table |
| `GET` | `/v1/stats` | Aggregate task/cost/latency/intervention stats |

Health and metrics:

| Path | Purpose |
|---|---|
| `GET /healthz` | Liveness — process is up |
| `GET /readyz` | Readiness — Postgres, Redis, and at least one model provider healthy |
| `GET /metrics` | Prometheus exposition |
| `GET /version` | Version, git SHA, schema revision, policy hash |

---

## 6. CLI surface

The CLI is a first-class client of the same API — it never reaches into the database directly. This guarantees the API is complete enough to build any other client (desktop app, voice, wearable) on.

```
astra serve                          # API + scheduler
astra worker --count 2               # worker processes
astra doctor                         # environment verification
astra db upgrade | check | reset

astra do "<instruction>" [--ceiling L2] [--dry-run] [--watch]
astra tasks [--status running]
astra show <task_id>
astra trace <task_id>
astra cancel <task_id> [--compensate]
astra reply <task_id> "<answer>"

astra approvals                      # interactive approval queue
astra approve <id> | reject <id> | modify <id> --set value=85

astra memory ingest <path> [--watch]
astra memory query "<q>" [--k 10] [--strategy hybrid]
astra memory entities [--type project]
astra memory forget <entity_id>

astra tools list | show <name> | invoke <name> --json '{...}'
astra policy show | reload | test '<tool>' '<json params>'
astra audit tail | verify
astra models list | bench
astra eval run <suite> | compare <run_a> <run_b>
```
