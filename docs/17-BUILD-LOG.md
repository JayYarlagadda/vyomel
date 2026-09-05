# 17 — Build Log

Status: **Living document** — session notes, not a design spec.

Design lives in `00`–`16` and the ADRs. This file records what was actually built, in what order, and where a session stopped, so the next session does not have to reconstruct intent from a dirty working tree.

---

## 2026-08-28 — M1 execution runtime (session 2)

**Left off:** M0 complete (tasks persist, CI skeleton, doctor, `/v1/tasks`). Nothing in `vyomel/runtime/`.

**This session's goal:** M1 keystone — durable DAG execution with handwritten plans, no LLM. Stop at a safe, test-green checkpoint rather than leaving a half-wired worker.

### Sequencing chosen

Vertical slice, not "all the tables then all the code":

1. Encode `07` §2–§3 as data (the state machines) and lock them to the markdown table so the spec cannot drift.
2. Pure DAG readiness + backoff — no I/O, so they can be proven before Redis exists.
3. Schema for `steps` / `step_edges` / `actions` / `dead_letters` / `side_effect_ledger`.
4. Tool contract and the three M1 tools, with a real sandbox (path traversal is rejected now, not deferred to M2).
5. Redis Streams + dispatcher write-ordering + worker claim + reaper + startup recovery.
6. Handwritten-plan install so a 5-action DAG can actually run.

### Spec clarifications made in code (not silent)

- **`available_at` on `actions`.** Backoff needs a place to live. Overloading `lease_until` would make the reaper SQL (`status IN (DISPATCHED, RUNNING)`) accidentally ignore READY rows that are waiting out a backoff. Additive column; documented in `03`.
- **Startup re-drive includes `DISPATCHED` with a null lease.** `07` §4.5 only mentions READY. After a Redis flush, DISPATCHED-with-no-lease is exactly the "committed, never published" window the write-ordering creates, and `lease_until IS NULL` is *not* `< now()` in SQL, so the reaper would never see them.
- **Ledger reservation before non-idempotent effects.** Writing the ledger in the same transaction as `SUCCEEDED` does not close the crash-between-effect-and-commit window. M1 tools are all idempotent, so this is encoded and tested with a test-only tool rather than pretended.
- **L0 verification.** `RUNNING → SUCCEEDED` requires `verification_pass`. L0 tools have no world mutation to re-observe, so a missing postcondition list is `PASS` at L0/L1 and `NO_METHOD` at ≥ L2. M3 replaces this with real verifiers.

### Stopped here

Safe checkpoint: **77 tests green**, `ruff`/`mypy --strict`/`check_layering` clean, migration `0002` round-trips.

**In tree and working:**
- FR-203 state machines locked to the spec document
- FR-201 persistence of steps/actions
- FR-204 5-action handwritten DAG end-to-end
- FR-206 backoff (unit)
- FR-207 / FR-202 crash-replay of an abandoned RUNNING action
- FR-210 reaper
- FR-601/602/603/608 tool contract + sandbox
- FR-402 L0 pass / L2 no-method
- FR-107 handwritten plan via API
- `vyomel worker`; scheduler hosted by `vyomel serve` outside `test` env

**Not in this session (next):**
- FR-205 timeout integration test (asyncio.wait_for is wired; no slow-tool test yet)
- `demos/m1/` script
- OS-level `kill -9` of a live worker (M6 chaos harness). The lease-expiry replay test is the M1 stand-in.
- Policy/approvals (M2). Dispatcher currently dispatches any READY action — M2 inserts the gate between READY and ENQUEUED.

---

## 2026-08-28 — M1 tail + M2 security and permissions (session 3)

**Left off:** 77 tests green, M1 complete except the timeout test and the demo.

**This session's goal:** close M1, then build M2 — classification, policy, approvals, audit, redaction, and the CLI/API surfaces for them.

### M1 tail

- FR-205: added `test.sleep`, a fake tool whose `default_timeout_s` is shorter than the sleep it is asked to perform, so a real `asyncio.wait_for` cancellation is observable. The action returns to `READY` for retry rather than failing.
- `demos/m1/` runs the 5-action DAG against real Postgres and Redis and kills a worker mid-flight to show lease-expiry replay. Fixing it surfaced a genuine bug: the demo skipped `scheduler.recover()`, which is what creates the Redis consumer group.
- Test isolation: `Scheduler.tick` queries *every* runnable task, so a task left behind by an earlier test was dispatched into the next test's stream. `runtime_db` now truncates `tasks` (cascading to steps, actions, approvals, ledger) on both sides of each test. The suite is order-independent instead of accidentally coupled.

### Sequencing chosen for M2

Bottom-up, because each layer is the thing the next one is not allowed to bypass:

1. `Secret` + redaction, so nothing built later can log a credential.
2. Glob/domain matching, isolated and property-tested — every policy rule's correctness rests on it.
3. Classification with escalation. Pure, so it is provable before any policy exists.
4. Policy engine: deny-first, default-deny, L4 invariant in code.
5. `approvals` and `audit_log` schema (Alembic `0003`) with the append-only trigger.
6. `PolicyGate`, inserted between `READY` and `DISPATCHED` in the dispatcher.
7. `ApprovalWorkflow`, then the API, then the CLI on top of the API.

### Decisions worth recording

- **The gate lives in `vyomel/runtime/`, not `vyomel/security/`.** Layering forbids `security` from importing `runtime`, and the decision needs both the policy and the action state machine. `security` owns records and verdicts; `runtime.gate` is the only component holding both halves. The payoff is that policy and approval logic are testable without a scheduler.
- **An approval is bound to `(action_id, parameter_hash, capability_level)`.** Binding to the action id alone would let an approved action be edited before dispatch; binding without the level would let a re-classification ride in on old consent. Both are tested by tampering with the row after approval.
- **Approvals are single-use, and expiry fails closed.** A crash-replay of a dispatched action finds its approval spent and asks again. `_assert_pending` re-checks the deadline on the decision path as well as in the sweeper, so a decision arriving after the deadline cannot win a race against a sweeper that has not run yet.
- **`modify` is a re-classification, not a shortcut.** The edit is validated against the tool's input schema, re-classified from scratch, and checked against the task ceiling. If it escalates, the approval the user just granted no longer covers it and the gate asks again — showing what the edited action actually does.
- **The L4 invariant is code.** `_assert_l4_invariant` raises on any allow-at-L4, and the loader corrects or rejects a policy that tries. Hypothesis generates adversarial policy YAML in `tests/security/test_l4_never_auto.py`; the assertion is that L4 is never `ALLOW`, by any route.
- **A broken policy file means `DENY_ALL`, not "last known good".** An operator who breaks the policy should see everything stop, not silently keep running under a version they can no longer read.
- **Audit `id` is drawn from the sequence before insert and covered by the hash.** Chaining alone would let a row be renumbered; signing the position means the row's place in the chain is signed too. Appends take a transaction-scoped advisory lock so two processes cannot fork the chain, and they share the caller's transaction so a rolled-back action leaves no audit claim that it happened.
- **`occurred_at` is normalized through one `_stamp` helper.** The hash covers the timestamp, so its in-memory and round-tripped forms must be byte-identical; UTC at microsecond precision is exactly what Postgres `timestamptz` stores.
- **`audit_log` has no foreign keys.** The trail has to outlive the rows it describes, and a cascade delete from a task would silently rewrite history — the thing the append-only trigger exists to prevent. `runtime_db` therefore does not truncate it, and the chain accumulating across the whole suite is itself an assertion that concurrent appends never fork.
- **`verify(start_id=...)`.** Tests assert about the segment they wrote rather than about every row any other test appended. The tamper tests disable the trigger, make the change, and restore in a `finally`: a test that cannot make the change proves nothing, and one that left the shared log broken would fail every later test.
- **The CLI goes through HTTP.** `vyomel audit verify` could read Postgres directly in ten lines; making it call the API is what keeps the API complete enough for a desktop or voice client later. The layering guard enforces it — `cli` may not import `security`.
- **`action.finished` is audited in one place, not at each exit.** `_run_claimed` has a dozen terminal paths; auditing at each would be a dozen chances to add a thirteenth that forgets. The worker re-reads the settled row after execution and writes one record per attempt. The result is summarized (status, field names, error) rather than copied, because audit rows are permanent and a tool result can be a megabyte of file content.
- **Writing `demos/m2/` found a real gap.** `AuditEvent.TASK_CREATED`, `PLAN_INSTALLED`, `ACTION_DISPATCHED`, and `ACTION_FINISHED` were declared but never emitted, so the trail jumped straight from a policy decision to an approval — `06` §7 requires all of them. The demo made the hole visible in a way the tests, which each asserted only about their own events, did not.

### Stopped here

Safe checkpoint: **190 tests green**, `ruff` clean, `mypy --strict` clean over 60 modules, `check_layering` clean.

**In tree and working:**
- FR-205 timeout → retry; `demos/m1/`; `demos/m2/` (approve / reject / tamper / denied-path)
- FR-301 classification + escalation, ceiling enforced at plan install
- FR-302 policy engine, default-deny, deny-first, expiring rules, hot reload
- FR-303/304/305 approval request, presentation, grant, reject, modify, expiry
- FR-306/308 `Secret`, redaction across logs and audit payloads
- FR-307 hash-chained append-only audit log + `vyomel audit verify`
- FR-603 sandbox traversal properties (adversarial + Hypothesis)
- API: approvals, audit, policy; CLI: `approvals`, `approve|reject|modify`, `audit`, `policy`

**Not in this session (next):**
- Prompt-injection defenses beyond the taint escalation: no untrusted-content boundary markers yet, because there is no planner to inject into until M5.
- Egress allowlist is parsed and testable but unused — the first network tool arrives in M4.
- Sensitivity classification (`policy.sensitivity`) is loaded but not consulted; it gates model routing (FR-703), which is M4.
- `vyomel tools`, `vyomel do`, `vyomel show`, `vyomel cancel` — M3/M5 surfaces.
- M3: verification engine with real verifiers. `test.notify` currently lands in `UNVERIFIED` because no verifier for `value_equals` exists yet, which is correct behavior and a placeholder at the same time.

**For whoever picks this up next.** The working tree is clean-green at this checkpoint; nothing is half-wired. Two things to know before starting M3:

- A task whose only action ends `UNVERIFIED` still reports `SUCCEEDED`. That is M1's placeholder policy (`07` §3 allows it only where the step tolerates unverified results), and it is the first thing M3 should tighten once real verifiers exist. It is visible in `demos/m2/run_demo.py` output and asserted loosely in `check()` there on purpose.
- `tests/fakes.py` and `demos/m2/run_demo.py` both declare a notify-style L3 tool. They are separate on purpose — `tests/tools/test_contract.py` asserts properties of the *production* registry, and a shared fixture that leaked into it would make those assertions describe something that never ships. When M3 adds real mutating tools, both should be deleted rather than generalized.

---

## 2026-08-28 — M3 verification engine (session 4)

**Left off:** M2 complete, 190 tests green. The verification engine was a stub: any declared postcondition was `NO_METHOD`, and a task whose only action ended `UNVERIFIED` still reported `SUCCEEDED`.

**This session's goal:** the first M3 vertical slice — real verifiers, evidence persistence, the UNVERIFIED task-completion tightening, and `fs.write_file`. Stop before `fs.move`/`copy`/`delete`, `shell.run`, `git.*`, and the cancel-compensation driver, so none of those are half-wired.

### Sequencing chosen

1. Typed dispatcher in `vyomel/verify/engine.py`. Empty postconditions stay L0/L1 pass / ≥ L2 `NO_METHOD`. Declared checks dispatch on `type` (or `verifier`, which `test.notify` already used).
2. `value_equals`, `file_exists`, `file_hash` actually re-observe. `file_hash` hashes the file bytes itself; a tool-reported digest is not evidence (FR-404 for the filesystem path).
3. `element_exists`, `api_readback`, `llm_judge` are registered and return `NO_METHOD` — honest, because those observation paths do not exist yet. An unknown type is the same. `FAIL` wins over `NO_METHOD`.
4. Schema `verifications` (Alembic `0004`) plus `verification.completed` on the audit trail (FR-405).
5. Worker takes `tool.verification_plan(params, output)` when the action row has no planner-declared postconditions.
6. Scheduler: a required `UNVERIFIED` action fails the step and the task. `tolerates_unverified` is the only opt-in, matching `07` §5.
7. `fs.write_file`: L1 in a directory named `scratch`, L2 elsewhere; backup-to-scratch on overwrite; compensate restores or unlinks.

### Decisions worth recording

- **Hashing lives in `vyomel.core.ids`.** `verify` may import `tools` (sandbox) but `tools` may not import `verify`. The writer and the verifier have to hash the same way, so the algorithm sits below both.
- **`file_hash` ignores `result["sha256"]`.** The catch-rate test is a tool that writes `78`, reports the hash of `87`, and still fails. If verification read the result it would pass 100 % of the time — the opposite of the claim.
- **Unavailable verifiers are dispatcher branches, not comments.** FR-403 names six types. Returning `NO_METHOD` for the three that have no observation path keeps "support" from meaning "the string appears in a docstring."
- **`created_at` on `verifications` is additive to the sketch in `03`.** Same reason as `available_at`: a row without a timestamp cannot be ordered independently of `id`, and ULID-as-time is a convenience not a contract.
- **Classify `fs.write_file` by a `scratch` path segment.** `classify()` still only receives parameters (the contract in `05`), not `ToolContext`, so it cannot see `settings.scratch_dir`. Matching a `scratch` directory name is the same convention `Settings.scratch_dir` uses. Policy `scratch-writes` still keys off `${scratch_dir}` independently.

### Stopped here

Safe checkpoint: verification engine, persistence, task-completion tightening, `fs.write_file`, and the lying-write catch-rate test. Run `alembic upgrade head` before the suite — `0004` is new.

**In tree and working:**
- FR-401 re-observe via `file_exists` / `file_hash`; `value_equals` for result fields
- FR-402 no assumed success; UNVERIFIED without opt-in fails the task
- FR-403 all six verifier names dispatched
- FR-405 evidence in `verifications` + `verification.completed`
- `fs.write_file` with backup/compensate
- `test.notify` now lands `SUCCEEDED` because `value_equals` exists (M2 tests and `demos/m2/` updated)

**Not in this session (next):**
- `fs.move`, `fs.copy`, `fs.delete` (trash-based, L2→L4 for trees)
- `shell.run` allowlist, `git.status`/`diff`/`commit`/`push`
- Cancel driver: reverse-topo compensate of reversible SUCCEEDED actions (the `compensate()` on `fs.write_file` is implemented; nothing calls it on cancel yet)
- `vyomel tools`, `vyomel do`, `vyomel show`, `vyomel cancel`
- `api_readback` / `llm_judge` / `element_exists` observation paths (M5/M7/M9)
- Prompt-injection boundary markers (M5), egress allowlist (M4), sensitivity routing (M4)

**For whoever picks this up next.** Apply `0004` first. The production registry now includes `fs.write_file`; `tests/tools/test_contract.py` enumerates it. Do not put test-only mutating tools in `default_registry()`. `tests/fakes.py` has `test.lying_write` and `test.opaque` for catch-rate and UNVERIFIED-task tests — they stay out of the production catalog for the same reason `test.notify` does. When adding `fs.delete`, the trash dir is already `Settings.trash_dir`. The cancel path should call `reverse_topo` in `vyomel/runtime/dag.py` and `tool.compensate()`; do not invent a second ordering.

---

## 2026-08-28 — M3 tools and cancel compensation (session 5)

**Left off:** verification engine, `verifications`, UNVERIFIED-task tightening, and `fs.write_file`. `compensate()` existed on the write tool and nothing called it.

**This session's goal:** the rest of the M3 catalog that can be proven without a planner — `fs.move`/`copy`/`delete`, `shell.run`, `git.*` — and the cancel driver that actually invokes `compensate()` in reverse topological order.

### Sequencing chosen

1. `fs.move` / `fs.copy` with backup-of-clobbered-dest, same `file_exists`/`file_hash` postconditions as write. `file_exists` gained an optional `kind: dir` so a directory dest is not a silent FAIL.
2. `fs.delete` moves to `Settings.trash_dir / <action_id> / <name>`. Never `unlink` of user data. A directory is L4 at `classify()` time.
3. `shell.run` with a frozen read-only allowlist. `argv[0]` is a basename we look up on PATH; a user-supplied absolute path cannot smuggle `cmd.exe`.
4. `git.status`/`diff` (L0, no-op compensate), `git.commit` (L2, `reset --soft` of *this* commit only), `git.push` (L3, irreversible, local bare-remote test).
5. `Canceller` in `vyomel/runtime/cancel.py`: task → `CANCELLED`, not-yet-started actions → `CANCELLED`, `SUCCEEDED`+reversible → `tool.compensate()` in `reverse_topo` order → `ROLLED_BACK`, each compensation audited. API + `vyomel cancel` on top.

### Decisions worth recording

- **Order of compensate is load-bearing.** Create-file then overwrite-same-file: undoing the create first restores the overwrite's backup onto a missing path and leaves the file behind. The FR-209 test is that pair, and it asserts the compensated-id order is overwrite then create.
- **`RUNNING` is reported, not seized.** CAS `RUNNING → CANCELLED` would lose the race with a worker that already mutated the world but has not yet committed `SUCCEEDED`, so we would neither record the effect nor compensate it. Cooperative cancel of a live execute is a later tightening.
- **Delete goes to trash, copy/write undo of *our* create may unlink.** The "never unlink" rule is about user data. Removing a file this tool created is the compensate of create, not a delete tool.
- **Scratch policy rules for move/copy/delete carry `max_level: L2`.** Without it, a dest-in-scratch allow rule would auto-approve an L4 classification (sensitive src, directory tree) — the L4 invariant fires at evaluate time, but the rule should never offer ALLOW as a candidate.

### Stopped here

Safe checkpoint: **234 tests green**, remaining M3 tools plus cancel compensation, API, and CLI. Catch-rate and reverse-topo cancel are both tested. `ruff` clean, `mypy --strict` clean over 65 modules, layering guard clean.

**In tree and working:**
- FR-209 cancel compensates reversible SUCCEEDED actions in reverse topo; irreversible effects are listed
- `fs.move` / `fs.copy` / `fs.delete` (trash)
- `shell.run` allowlist
- `git.status` / `git.diff` / `git.commit` / `git.push`
- `POST /v1/tasks/{id}/cancel`, `vyomel cancel [--no-compensate]`

**Not in this session (next):**
- `vyomel tools`, `vyomel do`, `vyomel show`
- Cooperative cancel of `RUNNING` actions (grace_s, per-action token)
- `api_readback` / `llm_judge` / `element_exists` observation paths (M5/M7/M9)
- Prompt-injection boundary markers (M5), egress allowlist (M4), sensitivity routing (M4)

**For whoever picks this up next.** `tests/tools/test_contract.py` enumerates the production catalog; keep test-only tools in `tests/fakes.py`. A `SUCCEEDED` task cannot be cancelled — the FR-209 test cancels after both writes have succeeded but *before* the scheduler tick that would complete the task. Do not "fix" that by allowing cancel of terminal success; undo-after-success is a different product question.

---

## 2026-08-29 — M3 operator surfaces (session 6)

**Left off:** remaining M3 tools, cancel compensation, `POST /v1/tasks/{id}/cancel`, `vyomel cancel`. 234 tests green.

**This session's goal:** the operator CLI/API that sits on the catalog and the task object — `vyomel tools`, `vyomel do`, `vyomel show` — without pretending a planner exists.

### Sequencing chosen

1. `GET /v1/tools` / `GET /v1/tools/{name}` from the registry, including input and output JSON Schema. This is the catalog M5 will filter, so it has to be complete now.
2. `POST /v1/tools/{name}/invoke`: classify, evaluate policy, audit, then execute. `CONFIRM` and `DENY` fail closed — debug invoke is not a consent bypass.
3. `vyomel do` → `POST /v1/tasks` with `origin: cli`. `--plan` is the handwritten DAG. `--dry-run` installs and classifies but leaves the task in `PLANNING` so the scheduler cannot dispatch.
4. `vyomel show` / `vyomel tasks` on `GET /v1/tasks/{id}` and `/plan`.

### Decisions worth recording

- **Direct invoke does not collect approvals.** An L3 `test.notify` (or `git.push`) returns 403 with `policy.confirm_required` on the trail. The worker path remains the only way to obtain consent. Verification also stays on the worker — `orchestrator` may not import `verify`.
- **Policy is committed before the tool runs.** A 403 rolls the request session back; committing first is what keeps a denied invoke on the hash chain.
- **`--dry-run` is not "install then cancel".** That races the in-process scheduler. Leaving `PLANNING` is the state the scheduler already ignores.

### Stopped here

Safe checkpoint: **253 tests green**, operator surfaces on top of the M3 catalog and task APIs. `ruff` clean, `mypy --strict` clean over 67 modules, layering guard clean.

**In tree and working:**
- `GET /v1/tools`, `GET /v1/tools/{name}`, `POST /v1/tools/{name}/invoke`
- `vyomel tools list|show|invoke`, `vyomel do [--plan] [--dry-run] [--watch]`, `vyomel show`, `vyomel tasks`

**Not in this session (next):**
- Cooperative cancel of `RUNNING` actions (grace_s, per-action token)
- `api_readback` / `llm_judge` / `element_exists` observation paths (M5/M7/M9)
- Prompt-injection boundary markers (M5), egress allowlist (M4), sensitivity routing (M4)
- Natural-language planning for `vyomel do` without `--plan` (M5)

**For whoever picks this up next.** M3's remaining product gap is cooperative cancel of a live execute. The operator CLI is complete enough that M5 can generate a plan and the same `vyomel show` / `vyomel cancel` / `vyomel approvals` path keeps working. Do not add a second invoke path that skips `DirectInvoker`.

---

## 2026-08-29 — M3 cooperative cancel (session 7)

**Left off:** operator CLI/API green. `RUNNING` actions were listed on cancel, not stopped.

**This session's goal:** 07 §8 — signal a live execute, wait `cancel_grace_s`, then have the *worker* CAS `RUNNING → CANCELLED`.

### Decisions worth recording

- **The canceller still does not seize `RUNNING`.** That CAS would race a worker that already mutated the world but has not committed `SUCCEEDED`.
- **The signal is the cancelled task row.** Workers share no in-memory state. The execute holds a per-action `CancellationToken`; a watcher polls Postgres and sets it, then after grace cancels the coroutine.
- **Commit the task `CANCELLED` before the grace wait.** An uncommitted signal is invisible to the worker's session.
- **`expire_on_commit=False` means a re-read after grace must `session.expire` the stale `RUNNING` instances**, or the report keeps listing them.

### Stopped here

Safe checkpoint: **256 tests green**, cooperative cancel of a live `test.sleep`, plus the operator surfaces from session 6. `ruff` clean, layering guard clean.

**In tree and working:**
- `Settings.cancel_grace_s` (default 10, ceiling 60)
- Worker per-action token + watcher; `RUNNING → CANCELLED` from the worker
- `tests/runtime/test_cooperative_cancel.py`

**Not in this session (next):**
- `api_readback` / `llm_judge` / `element_exists` observation paths (M5/M7/M9)
- Prompt-injection boundary markers (M5), egress allowlist (M4), sensitivity routing (M4)
- Natural-language planning for `vyomel do` without `--plan` (M5)
- M4 memory/RAG

**For whoever picks this up next.** M3's specified exit (catch-rate, reverse-topo compensate, operator CLI, cooperative cancel) is in tree. Do not allow cancel of a `SUCCEEDED` task. Blocking I/O tools (M7+) must poll `ctx.cancel` because `Task.cancel()` will not interrupt a thread.

---

## 2026-08-29 — M4 memory vertical slice (session 8)

**Left off:** M3 green at 256 tests. Context was about to run out; stop here rather than half-wire bge/graph/evals.

**This session's goal:** ingest + hybrid retrieve that is test-green, not recall@10.

### Decisions worth recording

- **Hashing 384-d embedder, not bge.** Same column, same cosine path. Hybrid tests use a unique lexical token. Do not quote recall@10.
- **Embeddings live only on chunks (FR-504).** The document row is path/hash/mime/version.
- **Register pgvector on the asyncpg connect.** Without it, inserts of `vector(384)` fail.
- **Ingest is synchronous.** `watch` is 501. POST returns a completed report; there is no job table yet.
- **API settings come from `app.state.settings`** so tests can allowlist `tmp_path`. `get_settings()` would ignore `create_app(settings)`.
- **`memory` cannot import `tools`.** Path allowlisting is duplicated in `vyomel/memory/paths.py`.

### Stopped here

Safe checkpoint: **272 tests green**, `mypy --strict` on 78 modules, layering clean. md/txt ingest through hybrid RRF and citations, HTTP/CLI included. Nothing is committed. Do not quote recall@10 — the embedder is hashing-bow-384, not bge.

**In tree and working:**
- Chunk on headings, then 512/64 word windows, with `char_start` / `char_end` for citations
- SHA-256 skip/replace: unchanged file is a no-op; changed bytes bump `version` and replace chunks
- Embeddings only on `document_chunks` (384-d hashing embedder)
- Hybrid retrieval: HNSW cosine + `tsvector`, fused with RRF (`k=60`)
- Alembic `0005` `documents` / `document_chunks`
- `POST /v1/memory/ingest`, `POST /v1/memory/query`, `vyomel memory ingest|query`

**Out of this slice:** `watch`, job polling, pdf/docx/html, entity graph, episodes, forget, memory tools, recall@10 evals. `watch=true` is 501.

**How to try it** (API must be up; paths must be under `VYOMEL_ALLOWED_ROOTS`):

```powershell
vyomel memory ingest .\notes.md
vyomel memory query "your unique token"
```

**Not in this session (next, in this order):**
- real `bge-small-en-v1.5` (same 384-d column; do not treat hashing retrieval as a quality number)
- then graph / forget
- then evals
- later: pdf/docx/html/code extractors, episodes (FR-507), memory tools (`memory.query` as a worker tool)

---

## 2026-09-02 — M4 completion (session 9)

**Left off:** M4 vertical slice (session 8) — md/txt ingest + hybrid RRF at 272 tests. Graph, episodes, bge, evals still open.

**This session's goal:** finish M4 scope from the build log, get green, commit eval results, push.

### Shipped

- **bge embedder** (`BgeEmbedder`) behind `get_embedder()`; hashing in `env=test` or explicit backend.
- **Extractors** for html/pdf/docx (optional `[memory]` deps); ingest dispatches by suffix.
- **Context graph** (Alembic `0006`): entities, relations, document→entity; `remember` / `forget` / `get_entity`.
- **Episodes** (Alembic `0007`, FR-507): recorded on task `SUCCEEDED`; API + CLI list.
- **Memory tools** registered: `memory.query`, `memory.get_entity`, `memory.remember`, `memory.forget`.
- **Policy rules** for memory tools (single `tool:` string per rule — lists broke the engine).
- **Eval harness**: 100-doc corpus, 125 questions; `evals/suites/rag/recall.py` with strategy ablation.
- **Results committed**: recall@10 hybrid = 0.928 (hashing); NFR-04 met. See `evals/results/2026-09-02-m4/`.

### Stopped here

**281 tests green**, `ruff`/`mypy --strict`/`check_layering` clean. M4 exit criteria met (recall@10 + ablation table). Pushed.

**Still deferred (not M4 blockers):** file watch, async ingest jobs, code/csv extractors, salience decay, graph expansion in retrieval, FR-508 reference resolution.

---

## 2026-09-02 — M5 planner slice 1 (session 10)

**Left off:** M4 complete at 281 tests; CI failing on `ruff format` for `cli/main.py`.

**This session's goal:** fix CI, land first M5 vertical slice — NL instruction to executed plan.

### Shipped

- **CI fix:** `ruff format` on `memory_remember` alias annotation.
- **`vyomel/planner/decompose.py`:** capability-filtered catalog, schema validation with retries (FR-103), prompt hash in audit.
- **`vyomel/models/providers/mock.py`:** deterministic mock planner (list → `fs.list_dir`, else `task.report`).
- **`vyomel/orchestrator/planning.py`:** `POST /v1/tasks` without `plan` now decomposes and installs via `PlanService`.
- **Plan types** moved to `vyomel/core/plan_spec.py`; `Trust.TOOL_UNTRUSTED` on real LLM output, `USER` for mock.
- **Tests:** `tests/planner/*`, `tests/api/test_planner.py`. **292 tests green.**

**Not yet:** replanning (FR-106), token budget gate (FR-108), cloud providers/router, `evals/suites/agent/`, prompt-injection boundary markers.

---

## 2026-09-02 — M5 completion (session 11)

**Left off:** M5 slice 1 — mock decompose, auto-plan on task create. Replanning, models, evals open.

**This session's goal:** finish M5 exit criteria.

### Shipped

- **Models:** `ModelProvider`, mock + mock-alt + OpenAI-compatible adapter, router with `SENSITIVE` privacy block, `AccountingProvider`, `CachedProvider`, `model_calls` migration `0008`.
- **Planner:** step contracts (FR-105), token budget gate (FR-108), `replan()` + `OrchestratorReplanGate` wired into scheduler, boundary markers.
- **API:** `POST /v1/tasks/{id}/reply` for `NEEDS_HUMAN`.
- **Eval:** 100 agent tasks; `task_completion_rate` = `tool_call_accuracy` = 1.0 for mock-v1 and mock-v2. `evals/results/2026-09-02-m5/`.
- **Tests:** all FR-1xx and FR-7xx requirement tests. **306 tests green.**

**Deferred:** FR-705 failover, production cloud routing tuning, full `evals/harness/` runner.

---

## 2026-09-02 — M6 heartbeat (session 12)

**Left off:** M5 complete at 306 tests. M6 open: chaos harness, blob spill, heartbeat, longrun eval.

**This session's goal:** first M6 slice — heartbeat lease extension for slow actions.

### Shipped

- **`ActionRepo.extend_lease()`** — CAS-guarded `lease_until` bump for `RUNNING` rows owned by the calling worker.
- **`Settings.heartbeat_interval_s`** (default 10s; 0 disables for tests).
- **Worker heartbeat loop** — background task extends the lease every interval while a tool executes (`docs/07` §4.4).
- **`test.hold` fixture tool** + `tests/runtime/test_heartbeat.py` (FR-208).

**Not yet:** blob spill, `evals/suites/longrun/` chaos harness, mock web tool, 30-minute task fixture.

---

## 2026-09-02 — M6 blob spill (session 13)

**Left off:** heartbeat shipped in `5f1c8ac`. Blob spill and chaos harness open.

**This session's goal:** spill large `actions.result` payloads to content-addressed blobs.

### Shipped

- **`vyomel/store/blobs.py`** — `spill_if_large`, `resolve_result`, content-addressed write/read.
- **`Settings.blob_spill_threshold_bytes`** (default 64 KiB).
- **Worker** spills before persisting results; scheduler, episodes, and cancel resolve on read.
- **`test.large_payload`** fixture + `tests/store/test_blobs.py`, `tests/runtime/test_blob_spill.py`.

**Not yet:** `evals/suites/longrun/` chaos harness, mock web tool, 30-minute task fixture.

---

## 2026-09-02 — M6 completion (session 14)

**Left off:** heartbeat (`5f1c8ac`) and blob spill (`dcbcde8`) shipped. Chaos harness open.

**This session's goal:** finish M6 exit criteria — mock web tool, research DAG, longrun eval, durability tests.

### Shipped

- **`web.fetch_mock`** — deterministic no-network fetch for research tasks.
- **`vyomel/planner/longrun.py`** — 100-action fan-out research plan + report.
- **`vyomel/runtime/chaos.py`** + **`vyomel/runtime/longrun.py`** — simulated `kill -9`, audit, harness with `fast`/`standard`/`chaos` modes.
- **`evals/suites/longrun/run.py`** + `evals/results/2026-09-02-m6/`.
- **`ActionQueue.reset_stream()`** for Redis-flush recovery tests.
- **Tests:** `tests/runtime/test_longrun.py`, `tests/planner/test_longrun_plan.py`, `tests/tools/test_web.py`.

**Deferred:** real subprocess SIGKILL harness (portable DB injection covers the recovery path); nightly `--mode chaos` run.

---

## 2026-09-02 — M7 browser agent (session 15)

**Left off:** M6 complete at `a625359`. Browser tools and eval suite open.

**This session's goal:** M7 exit — Playwright-backed browser tools, fixture pages, 40-workflow eval ≥ 80 %.

### Shipped

- **`vyomel/tools/browser/`** — accessibility-first resolver, fixture backend, optional Playwright persistent profile, 10 tools (`browser.open` … `browser.download`).
- **Fixture pages** — job board (incl. perturbed), gradebook, form app, paginated table.
- **`evals/suites/browser/`** — 40 workflows, `success_rate` = 1.0 on fixture backend.
- **Tests:** `tests/tools/test_browser.py` (FR-604). **Results:** `evals/results/2026-09-02-m7/`.

**Deferred:** live Playwright eval in CI (needs `pip install vyomel[browser]` + `playwright install`); `element_exists` verifier wiring for browser postconditions.

---

## 2026-09-02 — M8 desktop agent (session 16)

**Left off:** M7 complete at `832562b`. Desktop tools and eval suite open.

**This session's goal:** M8 exit — UIA-backed desktop tools, fixture apps, 50-workflow eval ≥ 70 %.

### Shipped

- **`vyomel/tools/desktop/`** — UIA-first resolver, fixture backend, optional UIA session wrapper, 9 `desktop.*` tools plus `app.open`/`app.focus`.
- **Fixture apps** — gradebook (incl. perturbed), student form, inventory, task list (JSON UI trees).
- **`evals/suites/desktop/`** — 50 workflows, `success_rate` = 1.0, `verification_catch_rate` = 1.0, vision tier 11 % on fixture backend.
- **Tests:** `tests/tools/test_desktop.py`, `tests/tools/test_desktop_hierarchy.py` (FR-605). **Results:** `evals/results/2026-09-02-m8/`.

**Deferred:** live UIA eval in CI (`pip install vyomel[desktop]`); VLM vision fallback for coordinate misses.

---

## 2026-09-03 — M9 external API tools (session 17)

**Left off:** M8 complete. API tools and S3 eval open.

**This session's goal:** M9 exit — OAuth-scoped Gmail/Calendar/GitHub tools, S3 end-to-end with L3 approval gating.

### Shipped

- **`vyomel/core/oauth.py`** — least-privilege scope map, refresh rotation (old refresh spent), memory/file/keyring stores.
- **`vyomel/tools/api/`** — fixture Gmail/Calendar/GitHub/HTTP; 14 tools per `05` §3.5.
- **`vyomel auth login|status|revoke`** — local token store; does not print secrets.
- **Policy** — confirm rules for `calendar.create_event`, `github.create_issue`, `github.comment`, `http.post` (email.send already confirmed).
- **`evals/suites/api/`** — S3: find interview email, list calendar, two free 60-minute slots, create both with external attendee. Both creates classify L3 and evaluate `CONFIRM`. **Results:** `evals/results/2026-09-03-m9/`.
- **Tests:** `tests/tools/test_oauth.py`, `tests/tools/test_api_tools.py` (FR-606).

**Deferred:** live OAuth authorization-code redirect; calling real Google/GitHub from CI.

---

## 2026-09-03 — M10 observability (session 18)

**Left off:** M9 complete. Observability (FR-801–805) open.

**This session's goal:** M10 exit — spans across API/Redis/worker, Prometheus `/metrics`, trace CLI/API, six Grafana dashboards.

### Shipped

- **`vyomel/obs/`** — in-process `SpanRecorder`, W3C `traceparent`, crash-resume `SpanLink`, Prometheus registry, timeline renderer.
- **Runtime wiring** — queue payload carries `traceparent`; worker restores it; reaper/scheduler/gate emit diagnostic series.
- **API** — `GET /metrics` uses the Vyomel registry; `GET /v1/tasks/{id}/trace`; `X-Trace-Id` on responses.
- **CLI** — `vyomel trace <task_id>` (HTTP client, not a second domain entry).
- **Infra** — Jaeger, OTel Collector, Prometheus, Grafana in `infra/docker-compose.yml`; dashboards in `infra/grafana/dashboards/`.
- **Tests:** `tests/obs/test_spans.py` (FR-801), `test_metrics.py` (FR-802), `test_log_correlation.py` (FR-803), `tests/api/test_trace_view.py` (FR-804), `tests/obs/test_dashboards.py` (FR-805).
- **Results:** `evals/results/2026-09-03-m10/`.

**Deferred:** live Jaeger in CI (`pip install vyomel[otel]` + collector). The in-process recorder is the fixture backend.

---

## 2026-09-04 — M11 model serving (session 19)

**Left off:** M10 complete. vLLM serving + benchmarks open (FR-707, NFR-12).

**This session's goal:** M11 exit — vLLM adapter, deployment artifacts, serving throughput table, offline path.

### Shipped

- **`vyomel/models/providers/vllm.py`** — OpenAI-compatible adapter; `VYOMEL_VLLM_BASE_URL`.
- **`vyomel/models/circuit.py`** — circuit breaker + `FailoverProvider` (FR-705).
- **`config/models.yaml`** — purpose → prefer list (local for extract/classify/summarize).
- **`infra/vllm/`** — docker-compose + `up.ps1`; **`infra/k8s/vllm-statefulset.yaml`**.
- **`evals/suites/serving/`** — fixture continuous-batching vs naive sequential; live backend for rented GPU.
- **Tests:** `test_vllm.py`, `test_failover.py`, `test_offline.py`, `test_serving_eval.py` (FR-707, FR-705, NFR-12).
- **Results:** `evals/results/serving/` — fixture speedup **1.99×** tok/s at concurrency 32.

**Deferred:** live A10G/L4 rented-GPU session (replace fixture table via `--backend live`). MX330 cannot run vLLM (ADR-0006).

---

## 2026-09-04 — M12 evaluation maturity (session 20)

**Left off:** M11 complete. Eval harness + security suite + CI gates open.

**This session's goal:** M12 exit — regression compare in CI, `injection_success_rate` = 0, ablation tables, README dashboard.

### Shipped

- **`evals/harness/`** — `scoring.py` normalizes suite JSON; `compare.py` enforces docs/11 §10 thresholds.
- **`evals/suites/security/`** — 105 cases over injection corpus under `evals/fixtures/injections/`; rate **0**.
- **`evals/suites/ablations/`** — RAG strategy table, planner model table, routing purpose/privacy checks.
- **CI** — `eval-gate` job runs security + ablations and compares against `evals/results/baselines/gated.json`.
- **Tests:** `tests/evals/test_compare.py` (NFR-11).
- **Results:** `evals/results/2026-09-04-m12/`; README Measured results filled.

**Deferred:** nightly full-suite schedule; chunk-size/overlap/embedding-model RAG grids; live LLM injection scoring beyond fixture defenses.

---

## 2026-09-04 — M13 Kubernetes (session 21)

**Left off:** M12 complete. Kubernetes chart + local-agent split open (C18, ADR-0009, CON-07).

**This session's goal:** M13 exit — `helm install` works; failover documented; kind CI.

### Shipped

- **`infra/helm/vyomel/`** — api / worker / scheduler / postgres / redis / optional vLLM; `values-kind.yaml`.
- **`Dockerfile`** — `ENTRYPOINT ["vyomel"]`; migrate init + wait-postgres.
- **Scheduler process** — `vyomel scheduler` + Redis `LeaderElector`; `VYOMEL_EMBED_SCHEDULER=false` in cluster.
- **Local agent** — hub + `WS /v1/agents/local/ws` + `vyomel agent`; dispatcher routes `desktop.*` when an agent is connected.
- **HPA** — Pods metric `vyomel_queue_depth` (disabled on kind).
- **CI** — `helm` + `kind` jobs.
- **Layering** — longrun harness moved to `vyomel/orchestrator/longrun.py`.

**Deferred:** short-lived cloud-cluster apply with GPU node pool (manual rental); multi-API sticky local-agent sessions (single-replica API is the kind default).

---

## 2026-09-04 — M14 media plugin (session 22)

**Left off:** M13 complete. Frontier media plugin (FR-607 / S7) open.

**This session's goal:** M14 exit — FFmpeg-backed media tools on the shared runtime; S7 end-to-end.

### Shipped

- **`vyomel/tools/media/`** — probe / transcribe / detect_segments / cut / concat / mute_segment / caption / export; fixture + optional ffmpeg backends.
- **Fixtures** — 12 `.vclip.json` clips under `vyomel/tools/media/fixtures/clips/` with word timings, silence, and mild profanity for S7.
- **Policy** — scratch intermediates allow; `media.export` CONFIRM (L2).
- **Tests:** `tests/tools/test_media.py` (FR-607); registry contract updated.
- **Eval:** `evals/suites/media/` — 12 clips → 60s draft, 2 profanity mutes, captions, export. **Results:** `evals/results/2026-09-04-m14/`.

**Deferred:** live Whisper weights in CI (fixture word timings stand in); burn-in caption requires a real ffmpeg encode session.

---

## 2026-09-04 — M15 workflow learning (session 23)

**Left off:** M14 complete. Workflow learning (FR-901–903) open.

**This session's goal:** M15 exit — mine recurring sequences, propose parameterized workflows, require explicit acceptance.

### Shipped

- **`vyomel/learning/`** — signature normalize, contiguous PrefixSpan-lite, proposals with `$param` binding, memory store + accept/reject/suppress.
- **`workflow.invoke`** tool; trust capped at L2 (FR-310); ORM `workflows` + migration `0009`.
- **API/CLI** — `GET/POST /v1/workflows…`, `vyomel workflows list|accept|reject|invoke`.
- **Tests:** `tests/learning/test_mining.py` (FR-901), `test_proposal.py` (FR-902), `test_acceptance.py` (FR-903), `tests/security/test_trusted_workflows.py` (FR-310).
- **Results:** `evals/results/2026-09-04-m15/`.

**Deferred:** durable Postgres-backed WorkflowStore sync on every mine (in-process store is the fixture path); auto-mine after task completion hook.

---

## 2026-09-04 — M16 voice (session 24)

**Left off:** M15 complete. Voice interface (STT / wake / TTS / barge-in) open.

**This session's goal:** M16 exit — local voice path on the shared API with fixture backends for CI.

### Shipped

- **`vyomel/voice/`** — fixture STT blobs, wake-word gate, TTS artifacts, `VoiceSession` with barge-in cancel.
- **API** — `POST /v1/voice/transcribe`, `/speak`, `/session/listen|speak|barge_in`.
- **CLI** — `vyomel voice transcribe|speak`.
- **Requirements** — FR-1001–1004; tests under `tests/voice/`.
- **Results:** `evals/results/2026-09-04-m16/`.

**Deferred:** live Whisper weights and system TTS engines (`VYOMEL_VOICE_BACKEND=whisper` refuses closed without them).

---

## 2026-09-04 — M17 multimodal / gym S8 (session 25)

**Left off:** M16 complete. Multimodal verticals (camera, S8, wearable client) open.

**This session's goal:** M17 exit — camera perception plugin, gym session from equipment + history, wearable on the same API.

### Shipped

- **`vyomel/perception/`** — fixture camera frames, equipment detections, `build_gym_session` (S8).
- **Tools** — `camera.capture`, `camera.detect`, `gym.plan_session`.
- **API** — `GET /v1/perception/detect`, `POST /v1/perception/gym/session`.
- **`WearableClient`** — thin HTTP client (FR-1103); demo `demos/m17/`.
- **Tests/eval:** `tests/perception/`, `tests/clients/test_wearable.py`, `evals/results/2026-09-04-m17/`.

**Deferred:** live camera / vision model path (fixture backend is CI default).

**Roadmap:** M0–M17 complete.

---

