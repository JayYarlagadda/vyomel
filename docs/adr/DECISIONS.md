# Architecture Decision Records

Baseline decisions ADR-0001 … ADR-0010 are consolidated here because they were made together during initial design. **Every subsequent decision gets its own file** (`ADR-00NN-short-title.md`) using the same template.

Template: Context → Options → Decision → Consequences → Status.

---

## ADR-0001 — Python 3.13 + FastAPI + Pydantic v2

**Status:** Accepted (2026-08-28)

**Context.** Vyomel needs an LLM/agent ecosystem, async I/O for long-running concurrent work, strong schema validation (tool contracts double as model-facing JSON Schema), and it must match the resume's stated stack.

**Options.** (a) Python; (b) Go — better concurrency story, matches the author's strongest language, but a far thinner LLM/vector/browser-automation ecosystem; (c) TypeScript — good browser story, weaker ML tooling; (d) Rust — best performance, worst iteration speed for this problem.

**Decision.** Python 3.13 with FastAPI, Pydantic v2, SQLAlchemy 2.0 async, `asyncio`.

**Consequences.** Fastest path to a working agent and the deepest library support. Costs: GIL limits CPU-bound parallelism (mitigated — the workload is I/O-bound, and blocking actuators go to a thread pool), and weaker static guarantees (mitigated with `mypy --strict`). Python 3.13 specifically, not 3.14, because of wheel availability (`13-ENVIRONMENT.md` C-4).

---

## ADR-0002 — PostgreSQL + pgvector as the single datastore

**Status:** Accepted (2026-08-28)

**Context.** The system needs transactional state (tasks, actions, approvals, audit) *and* vector search. These could live in one system or two.

**Options.** (a) Postgres + pgvector; (b) Postgres + a dedicated vector DB (Qdrant/Weaviate/Chroma); (c) SQLite + FAISS.

**Decision.** Postgres 17 with pgvector, HNSW indexes.

**Consequences.** One system to run, back up, and reason about. Crucially, retrieval can **join against relational state** in a single query — filter chunks by project entity, permission scope, or recency without a cross-store fan-out. A dedicated vector DB would win at 100M+ vectors; a single user's corpus is 4–6 orders of magnitude below that, so the operational cost is unjustified. SQLite was rejected because concurrent multi-process workers with row-level locking is exactly what Postgres does well and SQLite does not.

---

## ADR-0003 — Custom durable executor on Redis Streams + Postgres, not a workflow engine

**Status:** Accepted (2026-08-28)

**Context.** Vyomel needs durable, resumable, retryable DAG execution.

**Options.** (a) Temporal; (b) Prefect/Dagster; (c) Celery/Dramatiq; (d) custom: Postgres as source of truth + Redis Streams as transport.

**Decision.** (d).

**Consequences.** Temporal is the technically superior engine but brings a server, a worker SDK, and a programming model that would *own* the architecture — and would remove the most educationally and professionally valuable component of the project. Celery lacks first-class DAG semantics and durable state. Building it means owning correctness (leases, reapers, idempotency, write ordering, recovery), which is precisely the distributed-systems content that makes this project distinctive and interview-defensible. Chaos testing (`evals/suites/longrun/`) is the compensating control. Reassess if the system ever becomes multi-user.

---

## ADR-0004 — Playwright for browser control

**Status:** Accepted (2026-08-28)

**Options.** (a) Playwright; (b) Selenium; (c) raw CDP; (d) a browser extension.

**Decision.** Playwright (Python), with raw CDP available for anything it does not expose.

**Consequences.** Playwright gives accessibility-tree access (essential for the tier hierarchy), auto-waiting (removes a large class of flakiness), a persistent profile (session isolation from the user's daily browser), and ships its own browser binaries — so **no system Node.js is required**, which matters given this machine has none. An extension would offer deeper integration with the user's real session but is exactly the blast-radius expansion the security model is trying to avoid.

---

## ADR-0005 — Windows UI Automation as the primary desktop backend

**Status:** Accepted (2026-08-28)

**Context.** The original vision assumed macOS Accessibility + AppleScript. The development machine is Windows.

**Decision.** `uiautomation`/`pywinauto` over Windows UIA, behind a `DesktopBackend` interface, with `mss` for capture and OCR + VLM as the vision fallback.

**Consequences.** Structured element access (roles, names, automation IDs, value patterns) rather than pixel guessing, which is the foundation of the tier hierarchy and of verification. A macOS `AXUIElement` backend can be added later without touching the planner or runtime. UIA is slow on deep trees — mitigated with depth-bounded queries and cached tree snapshots.

---

## ADR-0006 — vLLM adapter local, vLLM benchmarks on rented GPU

**Status:** Accepted (2026-08-28)

**Context.** The resume claims vLLM. The development GPU is an MX330: 2 GB VRAM, compute capability 6.1. vLLM requires ≥ 7.0 and realistically ≥ 8 GB. Local vLLM is impossible, not merely slow.

**Options.** (a) drop the vLLM claim; (b) claim it without running it; (c) build the real adapter and deployment artifacts, and benchmark on a rented GPU.

**Decision.** (c). Option (b) is disqualified on integrity grounds — it is the exact failure the whole documentation approach exists to prevent.

**Consequences.** ~$5–20 of GPU rental buys a genuine, reproducible benchmark. The adapter, Docker artifacts, and K8s manifests are real code either way. The claim becomes "designed and deployed a self-hosted vLLM serving layer; benchmarked throughput and latency against an unbatched baseline" — precise and defensible. Day-to-day local inference uses llama.cpp with quantized ≤ 8B models.

---

## ADR-0007 — `bge-small-en-v1.5` local embeddings (384-d)

**Status:** Accepted (2026-08-28)

**Options.** (a) OpenAI `text-embedding-3-small` (1536-d); (b) `bge-small-en-v1.5` local (384-d); (c) `bge-large` local (1024-d).

**Decision.** (b), behind a pluggable interface, with the model name recorded per chunk so a future migration can re-embed incrementally.

**Consequences.** Documents never leave the machine during ingestion — a privacy property that a cloud embedding API cannot offer at any price. 384 dimensions keeps index size and HNSW query cost low. CPU throughput (~1–2k chunks/min) is adequate for a personal corpus. Retrieval quality is slightly below the largest models; the ablation study in `evals/suites/rag/` quantifies the gap instead of assuming it.

---

## ADR-0008 — OpenTelemetry for all telemetry

**Status:** Accepted (2026-08-28)

**Decision.** OTel SDK → OTel Collector → Prometheus (metrics) + Jaeger (traces); `structlog` for logs with trace correlation.

**Consequences.** Vendor-neutral, and it directly extends the author's production OTel work at Microsoft — making this a genuine depth area rather than a checkbox. The hard part is trace continuity across the Redis hop and across crash-and-resume; both are solved explicitly in `10-OBSERVABILITY.md` §2 and are themselves a differentiating detail.

---

## ADR-0009 — Split control plane and local actuator for Kubernetes

**Status:** Accepted (2026-08-28)

**Context.** Desktop automation is inherently host-bound: it needs a real session, a real screen, and real input. It cannot run in a pod. But the resume claims Kubernetes, and the API/scheduler/workers/vLLM genuinely benefit from it.

**Decision.** In the K8s topology, the control plane (API, scheduler, workers, Postgres, Redis, vLLM) runs in-cluster. A **local agent** on the user's machine holds an outbound WebSocket to the control plane, advertises host-only tools, and executes actions dispatched to it — the same architecture used by real desktop-agent products.

**Consequences.** Honest and defensible: "Kubernetes for the control plane; a local agent for host-bound actuation, because you cannot put a user's screen in a pod." The local agent introduces a new trust boundary — it authenticates with a scoped token, enforces its own capability ceiling locally, and never accepts an action above the ceiling the user configured on that machine.

---

## ADR-0010 — Verification is mandatory; `UNVERIFIED` is a first-class state

**Status:** Accepted (2026-08-28)

**Context.** The standard agent loop is `plan → act → assume success`. This is the primary source of silent, compounding failure: an agent that believes it entered a grade will confidently proceed to send a confirmation email.

**Options.** (a) trust the tool's return value; (b) verify only on failure signals; (c) mandatory re-observation and postcondition assertion for every ≥ L2 action.

**Decision.** (c), with `UNVERIFIED` as a distinct terminal-ish state so the system never has to choose between lying and failing.

**Consequences.** Latency and cost per action increase (one extra observation, sometimes one extra model call) — measured as `verification_duration_seconds`. In exchange, failures surface at the point of occurrence rather than three steps later, and `unverified_action_ratio` becomes a directly actionable reliability signal. This is the decision most responsible for Vyomel being trustworthy enough to grant real permissions to.
