# 15 — Risk Register

Status: **Living document — review at every milestone**

Severity × Likelihood → Priority. Anything **High/High** gets a mitigation implemented in the current milestone, not a future one.

---

## 1. Technical risks

| ID | Risk | Sev | Lik | Mitigation | Trigger to act |
|---|---|---|---|---|---|
| R-01 | **Desktop automation reliability is fundamentally poor** — many apps expose a weak or useless UIA tree, forcing vision fallback with low accuracy. | High | High | Enforce the tier hierarchy and *measure* `actuation_tier_distribution`. Choose fixture apps with good UIA. Report the honest success rate rather than cherry-picking demos. Scope M8 to workflows that are actually achievable. | Vision-tier ratio > 35 % |
| R-02 | **Prompt injection defeats the permission model** in a way that matters. | High | Med | Layered defenses (`06` §5): task capability ceiling, taint escalation, egress allowlist, approval gates. Measured continuously via `injection_success_rate`. Document residual gaps honestly. | Any non-zero injection success |
| R-03 | **The durable engine has a subtle correctness bug** — duplicate side effects under a rare interleaving. | High | Med | Idempotency keys + `side_effect_ledger` + explicit write ordering + chaos testing. Property-based tests over the state machine. | Any duplicate in chaos runs |
| R-04 | **Planner quality is too low** for multi-step tasks to complete reliably. | High | Med | Structured output + schema validation + capability-filtered catalog + bounded replanning. Measure per model config; route planning to the strongest available model. Accept a lower autonomy ceiling rather than a fake one. | `task_completion_rate` < 60 % |
| R-05 | **Retrieval quality is mediocre** on a personal corpus. | Med | Med | Hybrid retrieval, structure-aware chunking, graph expansion, reranking. Ablations identify what actually helps rather than guessing. | `recall@10` < 0.80 |
| R-06 | **Cost runs away** during development and evaluation. | Med | High | Hard cost ceilings per task; local models for high-volume purposes; response caching in deterministic mode; nightly rather than per-commit full evals. | Monthly spend > $50 |
| R-07 | **No usable local GPU** limits local-model quality and blocks local vLLM. | Med | Certain | Already realized. CPU 7B-Q4 for classification/extraction; rented GPU for serving benchmarks. Documented in `13-ENVIRONMENT.md` C-1 and `ADR-0006`. | — (accepted) |
| R-08 | **Playwright / UIA / OS updates break actuators.** | Med | Med | Pinned versions, fixture-based tests (never live third-party sites), CI catches breakage early. | CI failure after dependency bump |
| R-09 | **Postgres + pgvector performance degrades** as the corpus grows. | Low | Low | HNSW parameters tuned and benchmarked; corpus size is inherently bounded for a single user. | `retrieval_latency_p95` > 500 ms |
| R-10 | **Verification is unimplementable** for some action classes, pushing many actions to `UNVERIFIED`. | Med | Med | Multiple verifier types incl. `llm_judge`; track `unverified_action_ratio` as an SLO; prefer tools whose effects are readable. | Ratio > 10 % |
| R-11 | **Python 3.13 dependency gaps** for a needed library. | Low | Low | 3.13 has broad wheel coverage; fallback to 3.12 is a one-line venv change. Lockfile pinned. | Any hard blocker |
| R-12 | **Async + blocking actuator deadlocks** (UIA/FFmpeg starving the event loop). | Med | Med | Bounded thread pool, per-actuator semaphores, timeouts on every await, deadlock detection in tests. | Worker hang in CI |

---

## 2. Project risks

| ID | Risk | Sev | Lik | Mitigation |
|---|---|---|---|---|
| R-20 | **Scope collapse** — the vision is 15+ goals; a solo developer with a job and coursework stalls. | High | High | Milestone gates with hard exit criteria; explicit cut order in `12-ROADMAP.md` §5; each milestone independently demoable so partial completion is still a portfolio artifact. |
| R-21 | **Resume claims outrun the implementation** — the most damaging failure mode, because it surfaces in an interview. | **Critical** | Med | `14-RESUME-MAPPING.md` + `scripts/check_resume_claims.py`. A claim without a green linked test comes off the resume that day. |
| R-22 | **Docs rot** — documentation diverges from code and becomes misleading. | Med | High | Docs updated in the same PR as code (DoD item 4); `check_traceability.py` and `test_catalog_sync.py` fail CI on drift. |
| R-23 | **Demo-driven development** — polishing one impressive path instead of building the general system. | Med | Med | Evaluation suites over demos. Success is a distribution across 100 tasks, not one video. |
| R-24 | **Burnout on the hard middle** — M1 and M8 are grindy and unglamorous. | Med | Med | Interleave a visible-progress milestone after each hard one; keep the demo script requirement so every milestone produces something satisfying to run. |
| R-25 | **Competitive obsolescence** — a major lab ships this and the project feels pointless. | Low | High | The value is the demonstrated engineering, not market position. Occurrence is expected and does not reduce interview value. Track the space quarterly to keep framing current. |

---

## 3. Security and safety risks

| ID | Risk | Sev | Lik | Mitigation |
|---|---|---|---|---|
| R-30 | **Astra destroys real user data** during development. | High | Med | Dev runs against a sandboxed workspace root; `fs.delete` moves to trash rather than unlinking; L2+ confirm by default in dev; backups before destructive test suites. |
| R-31 | **Credentials leak** into git, logs, or a model prompt. | **Critical** | Med | `Secret` type, central redaction, `gitleaks` in CI, keyring for OAuth, `.env` git-ignored from commit #1, sensitivity-based local-only routing. |
| R-32 | **Astra sends something embarrassing** on the user's behalf (email/comment). | High | Low | L3 always requires approval; approvals show the full resolved payload; no batch approval above L2. |
| R-33 | **Screenshots containing private information** leave the machine via a cloud VLM. | High | Med | Credential-region detection and redaction before egress; `SENSITIVE` classification forces local-only; the local path fails closed rather than escalating. |
| R-34 | **Supply-chain compromise** in a dependency running inside a process that holds every credential. | High | Low | Pinned lockfile, `pip-audit` in CI, minimal dependency surface, dependency review on every addition. |

---

## 4. Accepted risks

Recorded deliberately so they are not re-litigated:

| Risk | Why accepted |
|---|---|
| No macOS support in v1 | Dev machine is Windows. The `DesktopBackend` interface keeps the door open at low cost. |
| Single-user only | Multi-tenancy adds substantial complexity and zero learning value for the target roles. |
| Local model quality is limited by CPU-only inference | Routing exists precisely to work around this; it is a design feature, not a defect. |
| Vision-tier actuation is unreliable | It is the last resort by design, measured and minimized rather than eliminated. |
| Some actions will remain `UNVERIFIED` | Honest labeling is strictly better than false success. |

---

## 5. Review cadence

Reviewed at every milestone gate. Each review: re-score likelihood from evidence, close realized-and-mitigated risks, open new ones discovered during the milestone, and confirm `14-RESUME-MAPPING.md` is still accurate. R-21 is checked **every** review without exception.
