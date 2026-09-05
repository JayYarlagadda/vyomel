# M15 workflow learning — 2026-09-04

Mine recurring action pipelines (support >= 3), propose parameterized workflows,
require explicit acceptance before invoke, suppress rejected patterns.

## Results

| metric | value |
|---|---:|
| success | True |
| proposals | 1 |
| occurrence_count | 5 |
| parameters | 5 |
| expanded steps | 4 |
| trust_level | L2 |
| suppression_ok | True |

## Reproduce

```powershell
python evals/suites/learning/run.py
pytest tests/learning tests/security/test_trusted_workflows.py
```
