# M5 agent planning eval — refreshed 2026-09-05 (resume-truth)

110 synthetic tasks (`evals/fixtures/agent/tasks.jsonl`): 50 list-directory,
50 summarize/report, plus **10 multi-step** “list then report” instructions.

## Results

| backend | task_completion_rate | tool_call_accuracy | schema_validity_rate | multi_step_accuracy |
|---|---:|---:|---:|---:|
| mock-planner-v1 | **1.000** | **1.000** | **1.000** | **1.000** |
| mock-planner-v2 | **1.000** | **1.000** | **1.000** | **1.000** |

Both configurations use the deterministic mock planner (no network). Cloud
providers are wired behind `VYOMEL_PLANNER_BACKEND=openai|local` for production.

`schema_validity_rate` validates the plan against `HandwrittenPlan` and each
action’s parameters against the tool’s Pydantic `Input` model (resume claim C9).
`multi_step_accuracy` covers the 10 dependent list→report fixtures (C2).

## Reproduce

```powershell
python evals/suites/agent/run.py --backend mock
python evals/suites/agent/run.py --backend mock-alt
```
