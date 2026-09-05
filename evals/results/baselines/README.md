# Eval baselines

Committed snapshot of gated metrics for CI. Update only when a measured improvement
is intentional and the corresponding suite results under `evals/results/` are
committed in the same change.

```bash
python -m evals.harness.compare \
  --baseline evals/results/baselines/gated.json \
  --candidate evals/results/2026-09-04-m12
```
