# M1 demo — durable DAG execution

Reproduces the M1 exit criterion: a hand-written 5-action DAG executes
correctly, and an action whose worker dies mid-flight completes exactly once.

No LLM is involved. The plan is written by hand in `run_demo.py`.

## Prerequisites

```powershell
docker compose -f infra/compose.yaml up -d   # Postgres + Redis
astra db upgrade                             # schema at head
astra doctor                                 # should be all-green
```

`ASTRA_ALLOWED_ROOTS` must contain `ASTRA_WORKSPACE_ROOT`; the demo writes its
sample files to `<workspace_root>/demo-m1` and the sandbox rejects reads
outside the allowlist. The defaults in `.env.example` already satisfy this.

## Run

```powershell
python demos/m1/run_demo.py           # straight run: 5 actions, all SUCCEEDED
python demos/m1/run_demo.py --crash   # abandon a claimed action, then recover
```

## What to look for

The DAG is a fan-out with a join, not a linear chain:

```
list ──► read notes ──┐
     └─► read rubric ─┴─► report
```

- Both reads are dispatched in the same scheduler tick (bounded parallelism).
- `task.report`'s output lands on `tasks.result` when the DAG completes — the
  scheduler copies it; tools never write task rows.
- With `--crash`, one action is forced to `RUNNING` with an expired lease, which
  is the row a `kill -9` between claim and result leaves behind. The reaper
  returns it to `READY`, a worker replays it, and it reports
  `SUCCEEDED after 2 attempts`. The `attempt_count` of 2 with a single
  successful result is the exactly-once claim, visible.

An OS-level `kill -9` of a separate worker process is the M6 chaos harness.
This demo injects the state that a kill produces, which is what recovery
actually has to handle.
