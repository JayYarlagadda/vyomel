# 16 — Development Workflow

Status: **Approved baseline (v1.0)**

---

## 1. Daily loop

```
  read docs/12-ROADMAP.md → current milestone
        ↓
  pick the next requirement ID (FR-xxx) from docs/01-REQUIREMENTS.md
        ↓
  branch:  feat/M1-action-state-machine
        ↓
  write the test first, marked  @pytest.mark.req("FR-203")
        ↓
  implement
        ↓
  ruff check --fix . && mypy . && pytest -q
        ↓
  update the docs touched by this change, in the SAME commit
        ↓
  commit → push → CI → merge
```

Docs in the same commit is non-negotiable. Documentation written later is documentation written wrong, and the traceability checks exist to make drift a build failure rather than a discovery six months on.

---

## 2. Branch and commit conventions

Branches: `feat/M<n>-<slug>`, `fix/<slug>`, `docs/<slug>`, `chore/<slug>`, `eval/<slug>`.

Conventional Commits, with the requirement ID in the body:

```
feat(runtime): enforce action state machine transitions

Implements FR-203. Illegal transitions raise IllegalTransition rather
than silently mutating status. The transition table mirrors
docs/07-EXECUTION-ENGINE.md §3 exactly.

Refs: FR-203, FR-201
```

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `chore`, `eval`, `sec`.

---

## 3. Definition of done (per change)

- [ ] Tests written first and passing, marked with the requirement ID
- [ ] `ruff check` clean, `ruff format` applied
- [ ] `mypy --strict` clean on touched modules
- [ ] Coverage not decreased; ≥ 85 % on `core`, `runtime`, `security`
- [ ] `scripts/check_layering.py` passes (no upward or cyclic imports)
- [ ] `scripts/check_traceability.py` passes (every P0 requirement has a test)
- [ ] Docs updated in the same commit
- [ ] No secrets added (`gitleaks` clean)
- [ ] If it changes behavior a metric tracks, the eval suite was re-run

---

## 4. Testing strategy

| Layer | Type | Speed | Runs |
|---|---|---|---|
| Pure logic (state machine, policy, DAG, classification) | unit, no I/O | ms | every save |
| Repositories, migrations | integration, real Postgres | seconds | every commit |
| Runtime (dispatch, leases, retries, recovery) | integration, real Postgres + Redis | seconds | every commit |
| Tools | contract tests + fixtures | seconds | every commit |
| API | httpx `ASGITransport` | ms | every commit |
| Planner | recorded model responses (cassettes) | ms | every commit |
| End-to-end | full stack, mock tools | ~1 min | pre-push |
| Evaluation suites | real models, scored | minutes–hours | nightly + pre-milestone |

Conventions: **real Postgres and Redis in integration tests**, never mocks — the bugs that matter live in transaction semantics and stream acknowledgement, and mocks cannot express them. Each test gets an isolated schema. Model calls in tests use recorded cassettes for determinism. Property-based tests (`hypothesis`) cover the state machine and the policy engine, since those are exactly the places where an unenumerated case is a security bug.

---

## 5. CI pipeline (GitHub Actions)

```
on: [push, pull_request]

  lint      ruff check · ruff format --check · mypy --strict
  guards    check_layering · check_traceability · check_catalog_sync
  security  gitleaks · pip-audit
  test      pytest with postgres+redis service containers, coverage gate
  eval-fast subset of evals (mock tools, ~5 min) + regression comparison
  build     package build · docker build · helm lint

nightly:
  eval-full all suites · compare against last baseline · commit results
```

A red CI is never merged around. If a check is wrong, the check gets fixed.

---

## 6. Repository layout

```
D:\Astra\
├─ README.md
├─ CHANGELOG.md
├─ pyproject.toml
├─ .env.example
├─ astra.toml                  # non-secret defaults
├─ docs/                       # this documentation set
│   └─ adr/
├─ astra/
│   ├─ core/                   # config, errors, ids, clock, types, logging
│   ├─ api/                    # FastAPI routers, schemas, deps
│   ├─ orchestrator/           # task/plan/approval services
│   ├─ planner/                # decomposition, DAG, replanning, budget
│   ├─ runtime/                # dispatcher, worker, state machine, queue, reaper
│   ├─ tools/                  # registry + fs, shell, git, web, browser, desktop, api, memory, media
│   ├─ verify/                 # verifiers, evidence
│   ├─ security/               # capability, policy, approvals, audit, redaction
│   ├─ memory/                 # ingestion, chunking, embedding, retrieval, graph, episodes
│   ├─ models/                 # providers, router, accounting, cache
│   ├─ perception/             # screen, uia, dom, ocr, clipboard
│   ├─ store/                  # models, repositories, migrations, seeds
│   ├─ obs/                    # tracing, metrics, logging setup
│   ├─ prompts/                # versioned prompt templates
│   └─ cli/                    # typer CLI
├─ tests/                      # mirrors astra/
├─ evals/                      # harness, suites, fixtures, results
├─ infra/                      # docker compose, k8s, helm, grafana, scripts
├─ scripts/                    # guards and dev utilities
└─ demos/                      # per-milestone demo scripts
```

---

## 7. Milestone ritual

At each milestone gate:

1. Run the full evaluation suite; commit results to `evals/results/`.
2. Update `14-RESUME-MAPPING.md` — flip boxes only for claims with green linked tests.
3. Review `15-RISKS.md`; re-score, close, and open risks.
4. Record a demo script in `demos/mN/`.
5. Update `CHANGELOG.md` and tag `mN`.
6. Write a short retrospective in `docs/retros/mN.md`: what was harder than expected, what was cut, what the next milestone should change.

The retrospective is not ceremony — it is where the next milestone's estimate gets corrected, and it is raw material for the blog series in `14-RESUME-MAPPING.md` §5.
