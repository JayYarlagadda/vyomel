# Serving results

Recorded: `2026-09-04T08:19:07.403599+00:00`
Backend: **fixture**

Synthetic OpenAI-compatible fixture: baseline serializes requests; vllm admits concurrency (continuous-batching shape). Replace with live A10G/L4 numbers via --backend live after infra/vllm/up.ps1.

## Throughput / latency

| System | Concurrency | req/s | tok/s | TTFT p50 | TTFT p95 | lat p95 |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 1 | 1.18 | 37.83 | 0.8178 | 0.822 | 0.8744 |
| baseline | 4 | 1.22 | 38.92 | 2.1005 | 2.1672 | 2.1673 |
| baseline | 8 | 1.22 | 38.89 | 3.9889 | 4.2806 | 4.2807 |
| baseline | 16 | 1.2 | 38.53 | 7.1926 | 7.7524 | 7.7525 |
| baseline | 32 | 1.23 | 39.4 | 13.478 | 14.3885 | 14.3885 |
| vllm | 1 | 1.17 | 37.33 | 0.8569 | 0.8934 | 0.8935 |
| vllm | 4 | 1.9 | 60.95 | 1.5062 | 2.1174 | 2.1175 |
| vllm | 8 | 2.18 | 69.89 | 2.3069 | 3.6437 | 3.6438 |
| vllm | 16 | 2.31 | 73.96 | 3.9384 | 6.6988 | 6.6989 |
| vllm | 32 | 2.45 | 78.25 | 7.0409 | 12.4478 | 12.4478 |

Continuous-batching throughput speedup at highest shared concurrency: **1.99x** vs naive sequential fixture.

## Reproduce

```powershell
# Fixture (CI / no GPU)
.\venv\Scripts\python.exe evals\suites\serving\run.py --backend fixture

# Live rented GPU (after infra/vllm/up.ps1)
.\venv\Scripts\python.exe evals\suites\serving\run.py --backend live --base-url http://localhost:8000/v1
```

See `docs/09-MODEL-SERVING.md` §5 and ADR-0006.
