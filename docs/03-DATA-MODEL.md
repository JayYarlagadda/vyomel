# 03 — Data Model

Status: **Approved baseline (v1.0)**
Engine: PostgreSQL 17 + `pgvector` 0.8+

---

## 1. Modeling principle

> **Structured state → relational tables. Semantic content → vector index. Never both, never neither.**

A common failure mode in agent projects is dumping everything into a vector store, which makes exact lookups ("which tasks are running?") unreliable and expensive. Astra keeps a strict split:

- **Relational**: tasks, steps, actions, approvals, audit, entities, relationships, files, workflows, model calls.
- **Vector**: document chunks and episodic summaries only — content whose *meaning* is queried.
- **Join key**: every chunk points back to a relational entity, so retrieval results always resolve to real objects.

---

## 2. Schema overview

```
                       ┌──────────────┐
                       │    tasks     │
                       └──────┬───────┘
                              │ 1:N
                       ┌──────▼───────┐
                       │    steps     │◄──┐ step_edges (DAG)
                       └──────┬───────┘   │
                              │ 1:N       │
                       ┌──────▼───────┐───┘
                       │   actions    │
                       └──┬────┬───┬──┘
              1:N         │    │   │        1:N
       ┌──────────────────┘    │   └───────────────────┐
       ▼                       ▼                       ▼
┌─────────────┐        ┌──────────────┐       ┌─────────────────┐
│ approvals   │        │verifications │       │  audit_log      │
└─────────────┘        └──────────────┘       └─────────────────┘

┌──────────────┐   entity_relations   ┌──────────────┐
│  entities    │◄────────────────────►│  entities    │
└──────┬───────┘                      └──────────────┘
       │ 1:N
┌──────▼───────┐        ┌─────────────────┐
│  documents   │───1:N──►│ document_chunks │  (vector(384))
└──────────────┘        └─────────────────┘

┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│  episodes    │   │  workflows   │   │ model_calls  │
│ (vector 384) │   │  (learned)   │   │ (accounting) │
└──────────────┘   └──────────────┘   └──────────────┘
```

---

## 3. Core execution tables

### 3.1 `tasks`

| Column | Type | Notes |
|---|---|---|
| `id` | `text` PK | ULID |
| `instruction` | `text` | verbatim user input |
| `normalized_intent` | `text` | planner's canonical restatement |
| `status` | `task_status` enum | see `07-EXECUTION-ENGINE.md` §2 |
| `origin` | `text` | `cli` \| `api` \| `voice` \| `schedule` \| `workflow` |
| `context_hints` | `jsonb` | caller-supplied hints (active app, selection, paths) |
| `plan_version` | `int` | increments on each replan |
| `replan_count` | `int` | enforced against `max_replans` |
| `token_budget` | `int` | total budget |
| `tokens_used` | `int` | running total |
| `cost_usd` | `numeric(12,6)` | running total |
| `max_wall_clock_s` | `int` | task deadline |
| `deadline_at` | `timestamptz` | computed at start |
| `result` | `jsonb` | final summary + citations |
| `error` | `jsonb` | structured terminal error |
| `trace_id` | `text` | root OTel trace |
| `created_at` / `started_at` / `finished_at` | `timestamptz` | |

Indexes: `(status, created_at)`, `(origin)`, `(trace_id)`.

### 3.2 `steps`

| Column | Type | Notes |
|---|---|---|
| `id` | `text` PK | ULID |
| `task_id` | `text` FK → tasks | `ON DELETE CASCADE` |
| `ordinal` | `int` | display order only, **not** execution order |
| `title` | `text` | human-readable |
| `intent` | `text` | what this step accomplishes |
| `status` | `step_status` enum | |
| `plan_version` | `int` | which plan generation produced it |
| `depends_on` | `text[]` | denormalized for fast readiness checks |
| `tolerates_unverified` | `bool` | if true, `UNVERIFIED` upstream still satisfies this step |

`step_edges(task_id, from_step_id, to_step_id, plan_version)` is the normalized DAG, with a CHECK preventing self-edges and an application-level acyclicity assertion at plan-validation time.

### 3.3 `actions`

The most important table. One row per tool invocation attempt lineage.

| Column | Type | Notes |
|---|---|---|
| `id` | `text` PK | ULID |
| `task_id` / `step_id` | `text` FK | |
| `tool` | `text` | registry name, e.g. `fs.read_file` |
| `tool_version` | `text` | for reproducibility across tool changes |
| `parameters` | `jsonb` | validated against the tool's input schema |
| `preconditions` | `jsonb` | asserted before execution |
| `postconditions` | `jsonb` | asserted by `astra.verify` after execution |
| `capability_level` | `capability` enum | `L0..L4` |
| `reversible` | `bool` | drives compensation on cancel |
| `idempotency_key` | `text` | **UNIQUE** — the duplicate-suppression mechanism |
| `depends_on` | `text[]` | action-level DAG; readiness is computed from this |
| `status` | `action_status` enum | see state machine |
| `attempt_count` | `int` | |
| `max_retries` | `int` | |
| `timeout_s` | `int` | |
| `lease_owner` | `text` | worker id holding the lease |
| `lease_until` | `timestamptz` | reaper reclaims after this |
| `available_at` | `timestamptz` | backoff gate; dispatcher will not enqueue before this. Distinct from `lease_until` so a retrying READY row cannot be mistaken for an expired lease. |
| `result` | `jsonb` | tool output |
| `error` | `jsonb` | `{code, message, retryable, observation}` |
| `span_id` | `text` | OTel span |
| `created_at` / `dispatched_at` / `started_at` / `finished_at` | `timestamptz` | |

Indexes: `(status, lease_until)` for the reaper, `(status, available_at)` for dispatch, `(task_id, status)`, `UNIQUE(idempotency_key)`, `(tool, status)`.

> **Idempotency key construction:** `sha256(tool || canonical_json(parameters) || task_id || step_id || plan_version)`. Deterministic across replays of the same logical action; distinct across genuinely different actions. Tools that are inherently non-idempotent (e.g. `email.send`) additionally consult a `side_effect_ledger` before executing.

### 3.4 `approvals`

| Column | Type | Notes |
|---|---|---|
| `id` | `text` PK | |
| `action_id` | `text` FK | one *live* approval per action: partial unique index `WHERE status = 'PENDING'`, since decided rows accumulate |
| `task_id` | `text` FK | the queue is read per task |
| `capability_level` | `capability` | compared again at consumption time, not trusted from the decision |
| `summary` | `text` | plain-language description of what will happen |
| `presented` | `jsonb` | the exact payload rendered to the user |
| `blast_radius` | `jsonb` | affected resources, reversibility, external visibility |
| `status` | `approval_status` | `PENDING \| APPROVED \| MODIFIED \| REJECTED \| EXPIRED` |
| `parameter_hash` | `text` | binds the approval to the invocation shown, not to the action |
| `modified_parameters` | `jsonb` | when the user edits before approving |
| `policy_rule_id` / `policy_hash` | `text` | attributes the gate to a rule in a policy version |
| `decided_by` / `decided_at` | `text` / `timestamptz` | |
| `consumed_at` | `timestamptz` | single-use; a crash-replay finds it spent and asks again |
| `expires_at` | `timestamptz` | fail-closed on expiry |

`presented` records what was actually shown rather than what the system meant to show — auditing intent would make the record useless in the one case that matters, a rendering bug that hid the true target of an action.

The `verifications` table landed in Alembic `0004`. Each row is one postcondition check against one action; the audit payload is a summary of the same evidence.

### 3.5 `verifications`

| Column | Type | Notes |
|---|---|---|
| `id` / `action_id` | `text` | |
| `verifier` | `text` | `value_equals`, `element_exists`, `file_exists`, `file_hash`, `api_readback`, `llm_judge` |
| `expected` / `observed` | `jsonb` | |
| `outcome` | `verify_outcome` | `PASS \| FAIL \| NO_METHOD` |
| `observation_tier` | `int` | 1=API … 4=vision; supports FR-404 |
| `evidence_ref` | `text` | path/hash of screenshot or response blob |
| `latency_ms` | `int` | |
| `created_at` | `timestamptz` | additive to the original sketch; a row without a timestamp cannot be ordered independently of `id` |

### 3.6 `audit_log`

Append-only. No `UPDATE`, no `DELETE`.

| Column | Type | Notes |
|---|---|---|
| `id` | `bigserial` PK | monotonic |
| `occurred_at` | `timestamptz` | |
| `actor` | `text` | `planner` \| `worker:<id>` \| `user` \| `policy` |
| `event_type` | `text` | `action.dispatched`, `policy.denied`, `approval.granted`, … |
| `task_id` / `action_id` | `text` | nullable |
| `capability_level` | `capability` | nullable |
| `payload` | `jsonb` | **redacted** before write |
| `prev_hash` / `hash` | `text` | hash chain: `hash = sha256(prev_hash || canonical(row))` |

Enforcement: a `BEFORE UPDATE OR DELETE` trigger raises an exception, and the hash chain makes silent tampering detectable. `astra audit verify` walks the chain.

---

## 4. Memory tables

### 4.1 `entities` — the personal context graph

| Column | Type | Notes |
|---|---|---|
| `id` | `text` PK | |
| `type` | `entity_type` | `person, project, document, application, task_ref, preference, workflow, organization, event, place` |
| `name` | `text` | canonical name |
| `aliases` | `text[]` | for reference resolution ("the Orbit repo") |
| `attributes` | `jsonb` | type-specific |
| `salience` | `real` | decayed recency/frequency score, used for ranking |
| `source` | `text` | how Astra learned it |
| `first_seen_at` / `last_seen_at` | `timestamptz` | |

Index: `GIN` on `aliases` and on `to_tsvector(name)`.

### 4.2 `entity_relations`

`(from_id, relation, to_id, confidence, evidence_ref, created_at)` with `relation ∈ {belongs_to, authored_by, mentions, depends_on, scheduled_for, located_at, related_to, derived_from}`. `UNIQUE(from_id, relation, to_id)`.

This is what makes "Continue Orbit from yesterday" resolvable: `project:Orbit → belongs_to → {repo, notes, benchmarks}` plus `episodes` filtered to yesterday.

### 4.3 `documents` and `document_chunks`

`documents(id, path, mime, content_hash, size_bytes, ingested_at, version)`

`entity_id` lands with the context graph (FR-502). `document_chunks` also store `embedding_model` so a later re-embed can be incremental (ADR-0007).

`document_chunks`:

| Column | Type | Notes |
|---|---|---|
| `id` | `text` PK | |
| `document_id` | `text` FK | |
| `ordinal` | `int` | position in document |
| `content` | `text` | chunk text |
| `token_count` | `int` | |
| `heading_path` | `text[]` | structural breadcrumb, e.g. `['Design','Retry Policy']` |
| `page` / `char_start` / `char_end` | `int` | for precise citation (FR-505) |
| `embedding` | `vector(384)` | `bge-small-en-v1.5` (tests use a 384-d hashing stand-in) |
| `embedding_model` | `text` | model id that produced `embedding` |
| `tsv` | `tsvector` | generated column for BM25/full-text |

Indexes:
```sql
CREATE INDEX ON document_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
CREATE INDEX ON document_chunks USING gin (tsv);
```

HNSW over IVFFlat: better recall at this corpus size and no retraining as documents are added incrementally.

### 4.4 `episodes` — episodic memory

`(id, task_id, entity_ids[], summary, outcome, started_at, finished_at, embedding vector(384))`

Written after every task completes. This is what lets Astra answer "what did I ask you to do about Orbit last week?" and is the raw input to workflow mining (FR-901).

### 4.5 `workflows`

`(id, name, description, source ∈ {user, learned}, definition jsonb, parameters jsonb, occurrence_count, accepted_at, trust_level)` — a learned or user-authored parameterized plan template. `trust_level` is capped at L2 per FR-310.

---

## 5. Supporting tables

| Table | Purpose |
|---|---|
| `model_calls` | `(id, task_id, action_id, provider, model, purpose, prompt_tokens, completion_tokens, ttft_ms, latency_ms, cost_usd, sensitivity, cache_hit)` — powers FR-704 and all cost/latency analysis. |
| `retrievals` | `(id, task_id, query, strategy, k, chunk_ids[], scores[], latency_ms)` — enables offline retrieval-quality scoring against labeled sets. |
| `side_effect_ledger` | `(idempotency_key, tool, external_ref, created_at)` — external effects that cannot be re-derived (email sent, event created), consulted before non-idempotent tools run. |
| `dead_letters` | `(action_id, reason, context jsonb, created_at)` — poison actions after retry exhaustion. |
| `permissions` | `(scope_type, scope_value, tool_pattern, decision, granted_at, expires_at)` — per-tool/app/domain grants (FR-309). |
| `secrets_meta` | `(name, backend, created_at, last_used_at)` — metadata only; values live in the OS keyring, never in Postgres. |
| `schema_migrations` | Alembic. |

---

## 6. Enumerations

```sql
CREATE TYPE capability      AS ENUM ('L0','L1','L2','L3','L4');
CREATE TYPE task_status     AS ENUM ('CREATED','PLANNING','READY','RUNNING','WAITING_FOR_USER',
                                     'PAUSED','SUCCEEDED','FAILED','CANCELLED','NEEDS_HUMAN');
CREATE TYPE step_status     AS ENUM ('PLANNED','READY','RUNNING','BLOCKED','SUCCEEDED','FAILED','SKIPPED');
CREATE TYPE action_status   AS ENUM ('PLANNED','READY','DISPATCHED','RUNNING','WAITING_FOR_USER',
                                     'SUCCEEDED','UNVERIFIED','FAILED','ROLLED_BACK','CANCELLED');
CREATE TYPE approval_status AS ENUM ('PENDING','APPROVED','MODIFIED','REJECTED','EXPIRED');
CREATE TYPE verify_outcome  AS ENUM ('PASS','FAIL','NO_METHOD');
CREATE TYPE entity_type     AS ENUM ('person','project','document','application','task_ref',
                                     'preference','workflow','organization','event','place');
```

---

## 7. Migration policy

1. Every schema change is an Alembic revision with a working `downgrade()`.
2. Migrations are **additive first**: add column → backfill → switch reads → drop old column in a later revision. Never a destructive single-step change.
3. `tests/store/test_migrations.py` runs `upgrade head` then `downgrade base` against a throwaway database in CI. A migration that cannot round-trip does not merge.
4. Enum values are only ever appended, never reordered or removed.
5. Seed/reference data lives in `astra/store/seeds/`, applied idempotently, never inside a migration.

---

## 8. Retention and privacy

| Data | Retention | Deletion path |
|---|---|---|
| `audit_log` | indefinite | never deleted (append-only, hash-chained) |
| screenshots / evidence blobs | 30 days default | `astra gc evidence` |
| `model_calls` prompts | **not stored** — only token counts, cost, and a prompt hash | n/a |
| `document_chunks` | until source deleted | `astra memory forget <entity>` cascades (FR-509) |
| `episodes` | 1 year default, configurable | cascades with `forget` |
| OAuth tokens | until revoked | OS keyring; `astra auth revoke <provider>` |

Prompt bodies are deliberately not persisted. Storing them would replicate the entire contents of the user's private documents into a second, less-protected location. Hashes are sufficient for cache lookups and reproducibility checks.
