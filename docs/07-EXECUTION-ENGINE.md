# 07 — Execution Engine

Status: **Approved baseline (v1.0)**

This is the systems core of Vyomel. It is what makes the project a distributed-systems project rather than a prompt-engineering project.

---

## 1. Goals

| Goal | Meaning |
|---|---|
| Durable | State survives worker crash, API restart, and machine reboot. |
| Resumable | A task interrupted at action 7 of 12 resumes at action 7, not action 1. |
| Exactly-once *effects* | Delivery is at-least-once; **effects** are deduplicated by idempotency key. |
| Concurrent | Independent DAG branches run in parallel under a bound. |
| Bounded | Every loop has a cap: retries, replans, steps, wall clock, tokens, cost. |
| Observable | One span per action; every transition is auditable. |
| Interruptible | Pause, resume, cancel, and human approval mid-flight. |

---

## 2. Task state machine

```
        CREATED
           │ plan requested
           ▼
       PLANNING ──────────────► FAILED        (plan invalid after 2 schema retries)
           │ plan validated
           ▼
         READY
           │ first dispatch
           ▼
        RUNNING ◄──────────────┐
        │  │  │  │             │
        │  │  │  └─► WAITING_FOR_USER ────────┘   (approval granted)
        │  │  │            │ rejected/expired
        │  │  │            ▼
        │  │  │          FAILED
        │  │  └─► PAUSED ─────┘                   (resume)
        │  │
        │  └─► NEEDS_HUMAN         (replan budget exhausted / unrecoverable ambiguity)
        │
        ├─► SUCCEEDED             (all terminal steps SUCCEEDED)
        ├─► FAILED                (any required step FAILED, no replan left)
        └─► CANCELLED             (user cancel; reversible actions compensated)
```

Terminal states: `SUCCEEDED`, `FAILED`, `CANCELLED`. `NEEDS_HUMAN` is *quasi-terminal* — resumable only by user input.

---

## 3. Action state machine (normative)

This table is the specification. `vyomel/runtime/state.py` encodes it directly, and `tests/runtime/test_state_machine.py` asserts that every illegal transition raises.

| From | To | Trigger | Guard |
|---|---|---|---|
| `PLANNED` | `READY` | dependencies satisfied | all upstream actions terminal-success |
| `PLANNED` | `CANCELLED` | task cancelled | — |
| `READY` | `WAITING_FOR_USER` | policy returned `CONFIRM` | approval created |
| `READY` | `FAILED` | policy returned `DENY` | audit written |
| `READY` | `DISPATCHED` | enqueued to Redis | row committed first |
| `WAITING_FOR_USER` | `READY` | approval `APPROVED` / `MODIFIED` | not expired |
| `WAITING_FOR_USER` | `FAILED` | `REJECTED` or `EXPIRED` | fail closed |
| `DISPATCHED` | `RUNNING` | worker claimed | lease acquired |
| `DISPATCHED` | `READY` | lease reaper | `lease_until < now()` |
| `RUNNING` | `SUCCEEDED` | tool ok **and** verification `PASS` | — |
| `RUNNING` | `UNVERIFIED` | tool ok, verification `NO_METHOD` | ≥ L2 only |
| `RUNNING` | `FAILED` | tool error non-retryable, or verification `FAIL`, or retries exhausted | — |
| `RUNNING` | `READY` | retryable error, retries remain | `attempt_count < max_retries`, backoff applied |
| `RUNNING` | `READY` | lease expired (worker died) | reaper; idempotency protects replay |
| `SUCCEEDED` | `ROLLED_BACK` | compensation on cancel | `reversible = true` |
| any non-terminal | `CANCELLED` | task cancelled | — |

**Rule that must never be violated:** there is no transition into `SUCCEEDED` that does not pass through verification. `UNVERIFIED` exists precisely so the system is never forced to lie (FR-402).

---

## 4. Dispatch and durability

### 4.1 Write ordering

```
BEGIN;
  UPDATE actions SET status='DISPATCHED', dispatched_at=now() WHERE id=$1 AND status='READY';
  -- (row-level guard: 0 rows updated => someone else took it, abort)
COMMIT;
XADD vyomel:actions * action_id $1        -- only after commit
```

Postgres commits **before** Redis. If the process dies between the two, the action sits in `DISPATCHED` with an expired lease and the reaper returns it to `READY`. The inverse ordering (Redis first) would allow a dispatched action with no durable record — unrecoverable. This ordering choice is the crux of NFR-03.

### 4.2 Worker claim

```
XREADGROUP GROUP workers worker-$id COUNT 1 BLOCK 5000 STREAMS vyomel:actions >

BEGIN;
  UPDATE actions
     SET status='RUNNING', lease_owner=$worker, lease_until=now() + timeout_s * interval '1 second',
         started_at=now(), attempt_count=attempt_count+1
   WHERE id=$1 AND status IN ('DISPATCHED','READY')
  RETURNING *;
COMMIT;
```

If zero rows return, the message is a duplicate delivery — the worker `XACK`s and moves on.

### 4.3 Idempotency

Before invoking a side-effecting tool:

```python
existing = await repo.find_succeeded_by_idempotency_key(action.idempotency_key)
if existing:
    return existing.result  # replay: return prior result, do not re-execute
```

For tools whose effects are externally visible and non-re-derivable (`email.send`, `calendar.create_event`, `git.push`), the tool additionally records into `side_effect_ledger` **inside the same transaction** that marks the action succeeded, and checks that ledger on entry. This closes the window where the effect happened but the status write was lost.

### 4.4 Lease reaper

Runs every 5 s in the scheduler process:

```sql
UPDATE actions
   SET status='READY', lease_owner=NULL, lease_until=NULL
 WHERE status IN ('DISPATCHED','RUNNING')
   AND lease_until < now()
   AND attempt_count < max_retries
RETURNING id;
```

Actions past `max_retries` go to `dead_letters` and `FAILED`. Long-running tools **heartbeat** by extending `lease_until`, so a legitimately slow action is not reaped.

### 4.5 Recovery on startup

The scheduler, on boot:
1. Re-drives `READY` actions into the Redis stream (Redis may have been flushed).
2. Claims pending-but-unacked stream entries via `XAUTOCLAIM` for dead consumers.
3. Runs one immediate reaper pass.
4. Recomputes DAG readiness for every `RUNNING` task.

This is what makes "restart the machine mid-task and it continues" true rather than aspirational.

**Clarification (M1):** Redis-flush recovery must also republish `DISPATCHED` rows whose `lease_until` is null. Those are exactly the "Postgres committed, XADD never happened" window that write-ordering creates, and `NULL < now()` is not true in SQL, so the reaper cannot see them. READY rows are picked up by the next dispatcher tick.

---

## 5. DAG execution

Readiness: an action is `READY` when every action it depends on is `SUCCEEDED` (or `UNVERIFIED` **and** the dependent step declares `tolerates_unverified = true`).

```
Task: "prepare backend application"

  A: find latest resume ──┐
                          ├──► D: compare & suggest edits ──► E: create modified copy
  B: fetch job posting ───┤                                        │
                          │                                        ▼
  C: extract requirements ┘                                   F: open application page
                                                                   │
                                                                   ▼
                                                              G: fill known fields
                                                                   │
                                                                   ▼
                                                       H: PAUSE for approval (L3)
```

A, B run in parallel. C depends on B. D depends on A and C. Parallelism is bounded by `max_parallel_actions` (default 4) and additionally by per-actuator concurrency limits — **desktop and browser actuators are limited to 1** because they share a single physical screen/session. This constraint is declared on the tool, not hardcoded in the scheduler.

---

## 6. Failure handling ladder

Escalation order. Each rung is tried before the next.

```
1. Tool-level retry        transient error, same parameters, exp backoff + jitter
                           (base 1s, factor 2, max 30s, max_retries=2)
                                │ still failing
2. Observe & adapt         re-observe environment; if the UI/state changed,
                           regenerate the action's selector/parameters and retry once
                                │ still failing
3. Step-level replan       planner receives {failed step, error, observation,
                           prior plan} and regenerates the affected subgraph only
                                │ replan_count >= max_replans (3)
4. Human escalation        task → NEEDS_HUMAN with a precise question and the
                           evidence gathered
```

Retryable vs non-retryable is a **property of the structured error** returned by the tool (FR-608), not a guess by the runtime:

| Code | Retryable | Example |
|---|---|---|
| `TIMEOUT` | yes | page load exceeded budget |
| `RATE_LIMITED` | yes (respect `retry_after`) | API 429 |
| `TRANSIENT_IO` | yes | network blip |
| `ELEMENT_NOT_FOUND` | yes → escalates to rung 2 | UI changed |
| `PRECONDITION_FAILED` | no → rung 3 | expected file missing |
| `PERMISSION_DENIED` | no | policy or OS refusal |
| `INVALID_PARAMETERS` | no → rung 3 | schema mismatch |
| `VERIFICATION_FAILED` | no → rung 3 | wrote 87, read back 78 |
| `UNSUPPORTED` | no | tool cannot do this |

---

## 7. Bounded autonomy

Every bound is configuration with a hard ceiling that config cannot exceed.

| Bound | Default | Hard ceiling | On breach |
|---|---|---|---|
| `max_retries` (per action) | 2 | 5 | `FAILED` + dead letter |
| `max_replans` (per task) | 3 | 5 | `NEEDS_HUMAN` |
| `max_steps` (per task) | 40 | 100 | `NEEDS_HUMAN` |
| `max_parallel_actions` | 4 | 16 | queued |
| `timeout_s` (per action) | 120 | 1800 | `FAILED(TIMEOUT)` |
| `max_wall_clock_s` (per task) | 3600 | 21600 | `FAILED(DEADLINE)` |
| `max_token_budget` | 200k | 2M | `FAILED(BUDGET)` |
| `max_cost_usd` | 2.00 | 20.00 | `FAILED(BUDGET)` |
| `approval_ttl_s` | 3600 | 86400 | approval `EXPIRED`, action `FAILED` |
| `cancel_grace_s` | 10 | 60 | worker cancels the execute coroutine and CAS-es `RUNNING → CANCELLED` |

The ceilings exist so that a misconfiguration cannot turn a bug into an unbounded, expensive, or destructive loop.

---

## 8. Cancellation and compensation

On cancel:
1. All `PLANNED` / `READY` / `DISPATCHED` actions → `CANCELLED`.
2. `RUNNING` actions receive a cooperative cancellation signal (per-action token on the worker, observed via the cancelled task row). After `cancel_grace_s` (default 10 s) the worker cancels the execute coroutine and moves the action to `CANCELLED`. The canceller does **not** CAS `RUNNING → CANCELLED` itself.
3. `SUCCEEDED` actions with `reversible = true` are compensated **in reverse topological order**, each compensation being itself an audited action.
4. Irreversible completed actions are reported explicitly in the cancellation summary: *"Cancelled. Note: the email to X was already sent and cannot be undone."*

Honest reporting of what could not be undone is a requirement, not a nicety.

---

## 9. Long-running tasks (FR-208)

- No client connection is required at any point. Clients poll `GET /v1/tasks/{id}` or subscribe to `WS /v1/tasks/{id}/events`.
- Progress is checkpointed at every action boundary.
- Large intermediate results spill to a content-addressed blob store (`.vyomel/blobs/<sha256>`) with only the reference stored in `actions.result`, keeping row sizes bounded.
- The scheduler is a separate process from the API, so API restarts and deploys never interrupt execution.
- The M6 acceptance test is literally: start a 100-item research task, `kill -9` the worker at a random point, restart it, and assert that the task completes with exactly 100 results and no duplicates.

---

## 10. Concurrency model

- `asyncio` event loop per worker process; N worker processes (default 2 in dev).
- Blocking actuators (Windows UIA, FFmpeg, OCR) execute in a bounded `ThreadPoolExecutor` so they cannot stall the loop.
- Per-actuator semaphores: `desktop=1`, `browser=1`, `fs=4`, `http=8`, `model=2`.
- Postgres access uses an async connection pool sized `2 × workers + 4`.
- No shared in-memory state between workers. All coordination is through Postgres and Redis — a worker is fully replaceable at any instant.
