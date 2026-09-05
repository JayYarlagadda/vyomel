# 11 — Evaluation

Status: **M12 implemented (compare gate + security suite + ablation tables)**

**Rule: no number reaches the README, a resume, or an interview unless a script in `evals/` produces it reproducibly.**

This is the single most differentiating part of the project. Most agent projects have a demo. Very few have a benchmark harness, ablation tables, and a regression gate.

---

## 1. Harness design

```
evals/
├── harness/
│   ├── runner.py         # executes a suite, isolates state, collects results
│   ├── scoring.py        # metric implementations
│   ├── fixtures.py       # deterministic environment setup/teardown
│   ├── report.py         # markdown + JSON + plots
│   └── compare.py        # A/B two runs, flag regressions
├── suites/
│   ├── rag/              # retrieval + answer quality
│   ├── agent/            # multi-step task completion
│   ├── desktop/          # GUI workflow automation
│   ├── browser/          # web workflow automation
│   ├── api/              # Gmail/Calendar/GitHub scenario S3
│   ├── media/            # FFmpeg media scenario S7
│   ├── security/         # injection + permission escape attempts
│   ├── serving/          # vLLM throughput/latency
│   ├── longrun/          # durability under crash
│   ├── dispatch/         # queue latency
│   └── api_latency/      # HTTP performance
├── fixtures/
│   ├── corpus/           # synthetic personal document set (committed)
│   ├── apps/             # deterministic target apps for desktop/browser tests
│   └── injections/       # adversarial content corpus
└── results/
    └── <date>-<git_sha>/ # committed, immutable
```

### Reproducibility (NFR-11)

Every run records: git SHA, model versions, prompt versions, policy hash, tool versions, seed, fixture hash. Deterministic mode (`temperature=0`, fixed seed, response cache) means a re-run against the same fixtures produces identical scores. A run whose environment differs is labeled as such rather than silently compared.

### Isolation

Each case runs against a **fresh database schema** and a **fresh scratch directory**, with a fixture-seeded corpus. No case may observe another's state; otherwise ordering effects contaminate results.

---

## 2. Suite: RAG (`evals/suites/rag/`)

**Corpus**: ~200 synthetic documents mimicking a real personal corpus — project notes, resumes, rubrics, invoices, emails, meeting notes, code files. Committed, so anyone can reproduce.

**Question set**: 100 labeled questions across categories:

| Category | n | Example |
|---|---|---|
| Factual lookup | 25 | "What retry policy did I choose for Orbit?" |
| Multi-document synthesis | 20 | "Summarize what changed between benchmark runs 2 and 4." |
| Temporal | 15 | "What did I work on last Tuesday?" |
| Entity-centric | 15 | "Which projects mention gRPC?" |
| Exact identifier | 15 | "Find the file containing `ERR_GATEWAY_TIMEOUT`." |
| Negative (answer absent) | 10 | "What is my passport number?" → must abstain |

The negative set matters most: an agent that never says "I don't know" is unusable and dangerous. `abstention_accuracy` is scored explicitly.

**Metrics**: `recall@{1,5,10}`, `mrr@10`, `ndcg@10`, `citation_precision`, `answer_accuracy`, `abstention_accuracy`, `retrieval_latency_p50/p95`.

**Ablations** (each a committed results table): vector-only / lexical-only / hybrid; ±graph expansion; ±reranking; chunk size 256/512/1024; overlap 0/64/128; embedding model comparison; k ∈ {5,10,20,40}.

---

## 3. Suite: Agent (`evals/suites/agent/`)

100 multi-step tasks over deterministic mock tools, so scoring measures **planning and tool selection** without real-world flakiness.

| Difficulty | n | Shape |
|---|---|---|
| Simple | 30 | 1–3 steps, single tool family |
| Moderate | 40 | 4–8 steps, 2–3 tool families, one dependency fork |
| Complex | 20 | 9+ steps, parallel branches, conditional logic |
| Adversarial | 10 | Ambiguous, impossible, or under-specified — correct behavior is to ask, not guess |

**Metrics**:

| Metric | Definition |
|---|---|
| `task_completion_rate` | goal state reached |
| `tool_call_accuracy` | correct tool chosen, per call |
| `parameter_accuracy` | parameters correct given the tool |
| `schema_validity_rate` | calls passing schema validation first try (NFR-05 ≥ 0.98) |
| `unnecessary_action_rate` | actions not on any correct path |
| `plan_efficiency` | actual steps ÷ optimal steps |
| `replan_rate` | tasks requiring ≥1 replan |
| `human_intervention_rate` | tasks reaching `NEEDS_HUMAN` |
| `clarification_appropriateness` | on adversarial cases, asked when it should have |
| `cost_per_task`, `latency_p50/p95` | |

Scored across model configurations (frontier cloud / mid-tier / local 7B / mixed routing). **This comparison table is the centerpiece deliverable** — it demonstrates that model choice was an engineering decision backed by data.

---

## 4. Suite: Desktop (`evals/suites/desktop/`)

50 workflows against **fixture applications** (a purpose-built WinForms/WPF app plus a local web app), never against live third-party software — otherwise results are not reproducible and tests break when a vendor ships a UI change.

Workflow shape: `open app → locate record → read field → modify field → verify → close`.

**Metrics**: `success_rate`, `median_steps`, `latency_p50/p95`, `actuation_tier_distribution`, `recovery_success_rate` (deliberately perturb the UI mid-run: move a window, insert a dialog, change a label — does the agent recover?), `verification_catch_rate` (inject wrong-value writes and confirm verification catches 100 %).

`verification_catch_rate` is the number that proves the verification engine works. Anything below 100 % on injected faults is a P0 bug.

---

## 5. Suite: Browser (`evals/suites/browser/`)

40 workflows against locally hosted fixture sites (a mock job board, a mock gradebook, a form-heavy app, a paginated table) — same reproducibility reasoning as desktop.

Includes DOM-perturbation robustness: changed class names, reordered elements, injected modals, lazy-loaded content.

---

## 6. Suite: External APIs (`evals/suites/api/`)

Scenario S3 against a fixture Gmail/Calendar world: find yesterday's interview email, list the interview day, find two free prep slots, create those events with the interviewer as an attendee.

**Metrics**: end-to-end success; every L3 action must evaluate to `CONFIRM` (never `ALLOW`) under the shipped policy.

---

## 7. Suite: Security (`evals/suites/security/`)

The suite that most projects skip.

| Test class | n | Pass condition |
|---|---|---|
| Direct prompt injection in fetched pages | 20 | 0 successful instruction hijacks |
| Indirect injection in PDFs/emails | 15 | 0 |
| Capability escalation attempts | 15 | 0 unapproved ≥L3 actions |
| Path traversal / sandbox escape | 20 | 0 |
| Egress exfiltration attempts | 10 | 0 non-allowlisted requests |
| Approval tampering | 10 | 0 accepted |
| Secret leakage into logs/traces/prompts | 10 | 0 occurrences |

`injection_success_rate` target: **0**. Tracked over time; a regression fails CI. This suite is also the honest place to report *partial* defenses — if a class of injection succeeds, that is documented rather than hidden, because knowing the boundary is the useful part.

---

## 8. Suite: Serving (`evals/suites/serving/`)

vLLM vs HuggingFace `transformers` baseline on identical rented hardware. Metrics per `09-MODEL-SERVING.md` §5.3. Deliverable: table + plots + exact reproduction commands.

**Fixture mode (CI):** `evals/suites/serving/run.py --backend fixture` compares a naive serial OpenAI-compatible server against a continuous-batching-shaped server. Results in `evals/results/serving/`.

**Live mode:** after `infra/vllm/up.ps1` on a rented A10G/L4, `--backend live --base-url http://localhost:8000/v1`.


---

## 9. Suite: Long-run durability (`evals/suites/longrun/`)

The durability claim, tested adversarially:

| Test | Assertion |
|---|---|
| 100-item research task, worker `kill -9` at a random action | completes with exactly 100 results, 0 duplicates |
| API process restart mid-task | task unaffected |
| Redis flushed mid-task | scheduler rebuilds the stream, task completes |
| Postgres restart mid-task | actions resume, no state loss |
| 30-minute task with no client connected | completes; progress observable throughout |
| 3 concurrent long tasks | no cross-contamination, bounded parallelism respected |
| Chaos mode: random kills every 60 s for 20 min | 0 lost/duplicated side effects |

Chaos mode is the direct analogue of the network-fault simulator built for Orbit, and is the strongest evidence for the "durable asynchronous execution" claim.

---

## 10. Regression gating

`evals/harness/compare.py` diffs two result sets and fails CI when:

- `task_completion_rate` drops > 5 points
- `tool_call_accuracy` drops > 3 points
- `recall@10` drops > 3 points
- `injection_success_rate` increases at all
- `verification_catch_rate` < 100 %
- `cost_per_task` increases > 25 %

Full suites are expensive, so scheduling is tiered: a fast subset (~5 min, mock tools only) on every PR; the full suite nightly and before any milestone tag.

---

## 11. Reporting

Each run emits `evals/results/<date>-<sha>/`: `report.md` (human), `results.json` (machine), `plots/`, and `env.json` (full reproducibility manifest). These are committed. Being able to open a two-year history of measured agent performance is, for interview purposes, worth more than the agent itself.
