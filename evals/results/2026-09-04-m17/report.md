# M17 multimodal S8 — 2026-09-04

Camera fixture detects gym equipment; personal history shapes today's session;
wearable client posts through the same HTTP task contract.

## Results

| metric | value |
|---|---:|
| success | True |
| equipment count | 5 |
| focus | pull_and_legs |
| blocks | 5 |
| wearable origin | api |

## Reproduce

```powershell
python evals/suites/gym/run.py
pytest tests/perception tests/clients/test_wearable.py
python demos/m17/run_demo.py
```
