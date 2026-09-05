# M12 — Evaluation maturity (2026-09-04)

## Regression gate

`evals/harness/compare.py` diffs candidate runs against `evals/results/baselines/gated.json`.
CI job `eval-gate` runs the security suite and fails the PR on any gated regression.

| Metric | Baseline | Tolerance |
|---|---|---|
| `task_completion_rate` | 1.000 | drop ≤ 5 pts |
| `tool_call_accuracy` | 1.000 | drop ≤ 3 pts |
| `recall_at_10` | 0.928 | drop ≤ 3 pts |
| `injection_success_rate` | 0.000 | any rise fails |
| `verification_catch_rate` | 1.000 | must stay 100 % |
| `cost_per_task` | 1.000 | rise ≤ 25 % |

## Security suite

105 cases across direct/indirect injection, capability escalation, path traversal,
egress, approval tampering, and secret leakage.

**`injection_success_rate` = 0.000**

Reproduce: `python evals/suites/security/run.py`

## Ablations

### RAG strategies (hashing-384, k=10)

| Strategy | recall@10 |
|---|---|
| hybrid | **0.928** |
| lexical | 0.920 |
| vector | 0.152 |

Source: `evals/results/2026-09-02-m4/`.

### Planner models (100 tasks)

| Config | completion | tool-call accuracy |
|---|---|---|
| mock-planner-v1 | 1.000 | 1.000 |
| mock-planner-v2 | 1.000 | 1.000 |

Source: `evals/results/2026-09-02-m5/`.

### Routing

| Check | Pass |
|---|---|
| extract / classify / summarize / embed prefer local | yes |
| sensitive input blocks remote backends | yes |
| offline mode blocks remote backends | yes |

Reproduce: `python evals/suites/ablations/run.py`
