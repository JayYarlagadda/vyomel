# M6 long-run durability eval — 2026-09-02

100-item mock research DAG (`web.fetch_mock` fan-out + `task.report`).
Worker death is simulated by abandoning a RUNNING row with an expired lease —
the same post-`kill -9` state the M1 demo injects.

## Results

| mode | items | simulated kills | fetch OK | lost | duplicates | pass |
|---|---:|---:|---:|---:|---:|---|
| fast | 10 | 3 | 10 | 0 | 0 | yes |
| standard | 100 | 42 | 100 | 0 | 0 | yes |

`chaos` mode (100 items, kill every 60 s for 20 min) is supported but not
committed here — run manually before a milestone tag:

```powershell
python evals/suites/longrun/run.py --mode chaos
```

## Reproduce

```powershell
python evals/suites/longrun/run.py --mode fast
python evals/suites/longrun/run.py --mode standard
pytest tests/runtime/test_longrun.py -m integration
```
