# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows milestones (`m0`, `m1`, …) rather than semver until v1.

## [Unreleased]

### M5 — Planner

- Model provider abstraction: `ModelProvider` protocol, mock + OpenAI-compatible adapters
- Model router with privacy hard-block (`SENSITIVE` never routes remote, FR-703)
- `model_calls` accounting table + `AccountingProvider` wrapper (FR-704)
- Deterministic response cache for eval (`CachedProvider`, FR-706)
- NL decomposition with schema retries, capability-filtered catalog, prompt hashes
- Step contracts on plan wire format (FR-105); token budget gate (FR-108)
- Bounded replanning on tool failure; `NEEDS_HUMAN` when exhausted (FR-106)
- Prompt-injection boundary markers for untrusted runtime context
- `POST /v1/tasks/{id}/reply` for `NEEDS_HUMAN` resume
- Agent eval: 100 tasks; `task_completion_rate` and `tool_call_accuracy` = 1.0
  for mock-v1 and mock-v2. Results in `evals/results/2026-09-02-m5/`.

### M4 — Memory and RAG

- Ingestion: md/txt/html/pdf/docx extractors (`astra/memory/extract.py`); structure-aware
  chunking; SHA-256 skip/replace (FR-506). Alembic `0005`–`0007`.
- Embeddings: `BgeEmbedder` via `sentence-transformers` (`[memory]` extra);
  `get_embedder()` selects hashing in test or `ASTRA_EMBEDDING_BACKEND=hashing|bge|auto`.
- Hybrid retrieval: HNSW cosine + `tsvector` GIN, fused with RRF k=60 (FR-503).
  Citations include path, heading_path, and char offsets (FR-505).
- Context graph: entities, relations, document→entity link (FR-502). `remember` /
  `forget` / `get_entity` via API, CLI, and worker tools.
- Episodic memory (FR-507): episodes recorded on task success; `GET /v1/memory/episodes`.
- Memory tools: `memory.query`, `memory.get_entity`, `memory.remember`, `memory.forget`.
- Eval: 100-doc synthetic corpus, 125 questions; recall@10 = 0.928 (hybrid, hashing).
  Ablation table in `evals/results/2026-09-02-m4/`. NFR-04 met.
- API: `POST /v1/memory/ingest|query|remember`, `GET /v1/memory/entities|episodes`,
  `GET|DELETE /v1/memory/entities/{id}`. CLI: `astra memory ingest|query|show|forget|remember|episodes`.
- Deferred to later milestones: file watch/async jobs, code/csv extractors, salience decay,
  graph expansion in retrieval, reference resolution (FR-508).

### M3 — Verification and first real tools (in progress)

- Verification engine dispatches on postcondition type (`astra/verify/engine.py`):
  `value_equals`, `file_exists`, and `file_hash` re-observe; `element_exists`,
  `api_readback`, and `llm_judge` are registered and return `NO_METHOD` until
  their observation paths exist. An unknown type is `NO_METHOD`, never a pass.
- `FAIL` wins over `NO_METHOD`. There is still no path to `SUCCEEDED` that
  skips verification.
- Verification rows persist in `verifications` (Alembic `0004`) with
  expected/observed evidence. Every check is also an `verification.completed`
  audit record.
- The worker takes the tool's `verification_plan()` after execute when the
  action has no planner-declared postconditions. `file_hash` hashes the file
  itself; a tool-reported digest is not evidence.
- A task whose only action ends `UNVERIFIED` is `FAILED` unless the step
  declared `tolerates_unverified`. M1's placeholder that reported `SUCCEEDED`
  is gone.
- `fs.write_file`: L1 inside a `scratch` directory, L2 elsewhere; backups
  prior content so compensate can restore; `file_exists` + `file_hash`
  postconditions.
- `fs.move` / `fs.copy` / `fs.delete`: L2, reversible. Delete moves to
  `Settings.trash_dir` and never unlinks; a directory tree classifies L4.
- `shell.run`: L0, read-only allowlist (`git status/diff/log`, `whoami`,
  `hostname`). `argv[0]` is looked up on PATH; a caller-supplied path is ignored.
- `git.status` / `git.diff` (L0), `git.commit` (L2, reverse via `reset --soft`
  of the commit this tool created), `git.push` (L3, irreversible).
- Cancel: `POST /v1/tasks/{id}/cancel` and `astra cancel` compensate reversible
  `SUCCEEDED` actions in `reverse_topo` order (FR-209). Irreversible completed
  effects are listed, not pretended undone.
- Operator surfaces: `GET /v1/tools`, `GET /v1/tools/{name}`,
  `POST /v1/tools/{name}/invoke` (policy-gated; `CONFIRM`/`DENY` fail closed),
  `astra tools list|show|invoke`, `astra do` (handwritten `--plan`, `--dry-run`
  leaves the task in `PLANNING`), `astra show`, `astra tasks`. Natural-language
  planning is still M5.
- Cooperative cancel of `RUNNING` actions: the worker holds a per-action
  `CancellationToken`, observes the cancelled task row, and after
  `cancel_grace_s` (default 10s, ceiling 60s) cancels the execute coroutine
  and CAS-es `RUNNING → CANCELLED`. The canceller does not seize `RUNNING`.

### M2 — Security and permissions

- Capability classification with saturating escalation (`astra/security/capability.py`):
  actuation tier, untrusted-content taint, bulk operations, and sensitive
  resources raise a level; nothing lowers one.
- Declarative policy engine (`astra/security/policy.py`): deny-first evaluation,
  per-level defaults, default-deny fallback, expiring rules, glob and domain
  matching, `${scratch_dir}`-style variables, and a hot-reloading `PolicyStore`.
- The L4 invariant is enforced in code, not configuration: any policy that would
  auto-approve L4 either fails to load or raises `PolicyInvariantViolation`.
  A malformed or unreadable policy file degrades to `DENY_ALL`.
- Approval flow (`approvals` table, Alembic `0003`): request, present, decide,
  modify, expire. Approvals are bound to `(action_id, parameter_hash,
  capability_level)`, are single-use, and fail closed on expiry.
- `PolicyGate` between `READY` and `DISPATCHED` (`astra/runtime/gate.py`), wired
  into the dispatcher and scheduler. `WAITING_FOR_USER` blocks the action and the
  task; a rejection fails both without a retry.
- A modification is re-validated against the tool schema and re-classified. An
  edit that raises the capability level does not inherit the approval it was
  granted under.
- Append-only hash-chained audit log (`audit_log`, Alembic `0003`): serialized
  appends via advisory lock, redaction before write, a `BEFORE UPDATE OR DELETE`
  trigger, and range-scoped chain verification.
- `Secret` wrapper (`astra/core/secrets.py`): no rendering path exposes the
  value, serialization raises, and construction registers the value with the
  redaction filter.
- API: `GET /v1/approvals`, `GET /v1/approvals/{id}`,
  `POST /v1/approvals/{id}/decide`, `GET /v1/audit`, `POST /v1/audit/verify`,
  `GET /v1/tasks/{id}/audit`, `GET /v1/policy`, `POST /v1/policy/reload`,
  `POST /v1/policy/test`.
- CLI: `astra approvals`, `astra approve|reject|modify`, `astra audit tail|verify`,
  `astra policy show|reload|test`. The CLI speaks HTTP only.
- Audit coverage now spans the whole lifecycle: `task.created`, `plan.installed`
  (with plan hash), every policy decision, every approval transition,
  `action.dispatched`, and `action.finished`.
- `demos/m2/` demonstrates approve, reject, tamper-after-approval, and a denied
  credential path, each asserting its own claim against a real database.

### M1 — Execution runtime

- Action and task state machines encoded as data and locked to
  `docs/07-EXECUTION-ENGINE.md` §3 (a docs/code drift test fails CI).
- Execution schema: `steps`, `step_edges`, `actions`, `side_effect_ledger`,
  `dead_letters` (Alembic `0002`). `available_at` is the backoff gate.
- DAG readiness, acyclicity, reverse-topo for compensation, full-jitter backoff.
- Redis Streams queue (consumer groups, claim, ack, `XAUTOCLAIM`).
- Dispatcher write-ordering: Postgres `READY → DISPATCHED` commits before `XADD`.
- Worker claim/execute/verify path; lease reaper; startup republish of orphan
  `DISPATCHED` rows.
- Tool contract + registry; `fs.read_file`, `fs.list_dir`, `task.report`;
  filesystem sandbox (fail closed).
- Handwritten plans via `POST /v1/tasks` `{plan: ...}` and `GET /v1/tasks/{id}/plan`.
- CLI: `astra worker`.

### M0 — Foundation
- Complete design documentation set in `docs/` (overview, requirements, architecture,
  data model, API, tools, security, execution engine, memory/RAG, model serving,
  observability, evaluation, roadmap, environment, resume traceability, risks, workflow, ADRs).
- Core primitives: settings with hard-ceiling enforcement, error hierarchy with
  declared retryability, ULID identifiers, deterministic idempotency keys,
  injectable clock, structured logging with mandatory secret redaction.
- Capability lattice (`L0`–`L4`) with saturating, monotonic escalation.
- Persistence: async SQLAlchemy engine, unit-of-work session scope, `tasks` table,
  Alembic migration `0001` creating extensions and shared enums.
- FastAPI application with `/healthz`, `/readyz`, `/version`, `/metrics`, and
  `POST|GET /v1/tasks`.
- Typer CLI: `serve`, `doctor`, `db upgrade|downgrade|current|check`.
- Local infrastructure: Postgres 17 + pgvector and Redis 7 via Docker Compose in WSL.
- CI: ruff, mypy strict, layering guard, traceability guard, gitleaks, pip-audit,
  pytest against real Postgres and Redis, migration round-trip check.
- Permission policy baseline in `config/policy.yaml`.
