# M2 demo — an L3 action blocks for approval

Reproduces the milestone's exit criterion: an action that needs human consent
stops, shows what it would do, and proceeds only after a decision.

```powershell
python demos\m2\run_demo.py                # approve      -> the effect happens, once
python demos\m2\run_demo.py --reject       # reject       -> action and task fail, no effect
python demos\m2\run_demo.py --tamper       # edit after approval -> consent is void
python demos\m2\run_demo.py --denied-path  # credential path -> denied, never presented
```

Prerequisites: Postgres and Redis up (`docker compose -f infra/compose.yaml up -d`)
and the schema migrated (`vyomel db upgrade`).

Each mode asserts its own claim and exits non-zero if the claim fails, so this
doubles as a smoke test of the whole permission path against a real database.

## What each run shows

`--approve` (default) prints the approval as the user would see it — summary,
resolved parameters, capability level, blast radius, the deciding policy rule and
policy hash, and the expiry — then the effect firing exactly once, then the audit
trail with its chain verified.

`--reject` shows the action failing with `PERMISSION_DENIED` and no retry. A
denied action is not retried, because retrying a denial is just a slower denial.

`--tamper` approves, then edits the action's parameters before dispatch. The
approval is bound to a hash of the parameters that were shown, so it no longer
applies: the action returns to `WAITING_FOR_USER` with a second, fresh approval
pending and the first one unconsumed.

`--denied-path` targets a credential path. It classifies `L4` *and* matches a
deny rule, and deny wins — so no approval is created at all. There is nothing a
human could usefully consent to, and asking would train the user to click
through warnings.

## Note on the demo tool

The demo registers `demo.notify`, an L3 non-idempotent tool with a visible
external effect. Nothing in the production catalog is above L1 yet (the mutating
tools arrive in M3), so demonstrating a gate requires something to gate. It is
declared in the demo rather than by relaxing `config/policy.yaml`, because a demo
that weakened the mechanism it demonstrates would prove the opposite of the point.
