# M5 agent planning eval — 2026-09-02

100 synthetic tasks (`evals/fixtures/agent/tasks.jsonl`): 50 list-directory
instructions, 50 summarize/report instructions.

## Results

| backend | task_completion_rate | tool_call_accuracy |
|---|---:|---:|
| mock-planner-v1 | **1.000** | **1.000** |
| mock-planner-v2 | **1.000** | **1.000** |

Both configurations use the deterministic mock planner (no network). Cloud
providers are wired behind `ASTRA_PLANNER_BACKEND=openai|local` for production.

## Reproduce

```powershell
python evals/suites/agent/run.py --backend mock
python evals/suites/agent/run.py --backend mock-alt
```
