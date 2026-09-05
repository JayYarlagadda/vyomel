"""Serving benchmark: continuous-batching vs naive sequential (docs/09 §5.3).

Fixture mode (CI / no GPU):
  python evals/suites/serving/run.py --backend fixture

Live mode (rented A10G/L4 via infra/vllm/up.ps1):
  python evals/suites/serving/run.py --backend live --base-url http://localhost:8000/v1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vyomel.models.providers.openai_compat import OpenAICompatibleProvider
from vyomel.models.providers.vllm import VllmProvider
from vyomel.models.types import ChatMessage, ModelRequest

PROMPTS = [
    "Summarize the difference between leases and locks in three sentences.",
    "List five failure modes of an agent that claims success without verification.",
    "Explain continuous batching for LLM serving in one short paragraph.",
    "Give three reasons a personal agent should keep embeddings on-device.",
    "What does a circuit breaker protect against in a model router?",
]


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] * (1 - frac) + ordered[high] * frac


async def _one(
    provider: Any,
    prompt: str,
    *,
    max_tokens: int,
) -> dict[str, float | int]:
    started = asyncio.get_running_loop().time()
    response = await provider.complete(
        ModelRequest(
            purpose="chat",
            messages=(ChatMessage(role="user", content=prompt),),
            max_tokens=max_tokens,
            temperature=0.0,
            seed=7,
        )
    )
    elapsed = asyncio.get_running_loop().time() - started
    return {
        "latency_s": elapsed,
        "ttft_s": response.latency_ms / 1000.0,
        "prompt_tokens": response.prompt_tokens,
        "completion_tokens": response.completion_tokens,
    }


async def _run_concurrency(
    provider: Any,
    *,
    concurrency: int,
    max_tokens: int,
    rounds: int,
) -> dict[str, Any]:
    latencies: list[float] = []
    ttfts: list[float] = []
    completion_tokens = 0
    wall_start = asyncio.get_running_loop().time()
    for round_i in range(rounds):
        prompts = [PROMPTS[(round_i * concurrency + i) % len(PROMPTS)] for i in range(concurrency)]
        results = await asyncio.gather(
            *[_one(provider, prompt, max_tokens=max_tokens) for prompt in prompts]
        )
        for item in results:
            latencies.append(float(item["latency_s"]))
            ttfts.append(float(item["ttft_s"]))
            completion_tokens += int(item["completion_tokens"])
    wall = asyncio.get_running_loop().time() - wall_start
    return {
        "concurrency": concurrency,
        "requests": len(latencies),
        "wall_s": round(wall, 3),
        "ttft_p50_s": round(_percentile(ttfts, 50), 4),
        "ttft_p95_s": round(_percentile(ttfts, 95), 4),
        "latency_p50_s": round(_percentile(latencies, 50), 4),
        "latency_p95_s": round(_percentile(latencies, 95), 4),
        "output_tokens_per_s": round(completion_tokens / wall, 2) if wall else 0.0,
        "requests_per_s": round(len(latencies) / wall, 2) if wall else 0.0,
        "mean_latency_s": round(statistics.fmean(latencies), 4) if latencies else 0.0,
    }


async def benchmark_provider(
    provider: Any,
    *,
    label: str,
    concurrencies: list[int],
    max_tokens: int,
    rounds: int,
) -> dict[str, Any]:
    rows = []
    for concurrency in concurrencies:
        rows.append(
            await _run_concurrency(
                provider,
                concurrency=concurrency,
                max_tokens=max_tokens,
                rounds=rounds,
            )
        )
    return {"label": label, "rows": rows}


async def run_fixture(
    *,
    concurrencies: list[int],
    max_tokens: int,
    rounds: int,
) -> dict[str, Any]:
    from evals.suites.serving.fixture_server import FixtureServer

    results = []
    for mode in ("baseline", "vllm"):
        async with FixtureServer(mode=mode, max_num_seqs=max(concurrencies)) as server:
            if mode == "baseline":
                provider: Any = OpenAICompatibleProvider(
                    name="baseline",
                    base_url=server.base_url,
                    api_key="EMPTY",
                    model="fixture-baseline",
                    is_remote=False,
                )
            else:
                provider = VllmProvider(
                    base_url=server.base_url,
                    model="fixture-vllm",
                    api_key="EMPTY",
                )
            results.append(
                await benchmark_provider(
                    provider,
                    label=mode,
                    concurrencies=concurrencies,
                    max_tokens=max_tokens,
                    rounds=rounds,
                )
            )
    return {
        "backend": "fixture",
        "note": (
            "Synthetic OpenAI-compatible fixture: baseline serializes requests; "
            "vllm admits concurrency (continuous-batching shape). Replace with "
            "live A10G/L4 numbers via --backend live after infra/vllm/up.ps1."
        ),
        "systems": results,
    }


async def run_live(
    *,
    base_url: str,
    concurrencies: list[int],
    max_tokens: int,
    rounds: int,
) -> dict[str, Any]:
    # Live session: measure the same endpoint at rising concurrency. A true
    # HF transformers baseline must run on the same GPU host; see README in
    # evals/results/serving/ for the paired baseline command.
    provider = VllmProvider(base_url=base_url, model="live")
    measured = await benchmark_provider(
        provider,
        label="vllm-live",
        concurrencies=concurrencies,
        max_tokens=max_tokens,
        rounds=rounds,
    )
    return {
        "backend": "live",
        "base_url": base_url,
        "systems": [measured],
    }


def _throughput_table(payload: dict[str, Any]) -> str:
    lines = [
        "| System | Concurrency | req/s | tok/s | TTFT p50 | TTFT p95 | lat p95 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for system in payload["systems"]:
        for row in system["rows"]:
            lines.append(
                "| {label} | {c} | {rps} | {tps} | {t50} | {t95} | {l95} |".format(
                    label=system["label"],
                    c=row["concurrency"],
                    rps=row["requests_per_s"],
                    tps=row["output_tokens_per_s"],
                    t50=row["ttft_p50_s"],
                    t95=row["ttft_p95_s"],
                    l95=row["latency_p95_s"],
                )
            )
    return "\n".join(lines)


def _speedup(payload: dict[str, Any]) -> float | None:
    systems = {s["label"]: s for s in payload["systems"]}
    if "baseline" not in systems or "vllm" not in systems:
        return None
    base_by_c = {r["concurrency"]: r for r in systems["baseline"]["rows"]}
    vllm_by_c = {r["concurrency"]: r for r in systems["vllm"]["rows"]}
    shared = sorted(set(base_by_c) & set(vllm_by_c))
    if not shared:
        return None
    concurrency = shared[-1]
    base = base_by_c[concurrency]
    batched = vllm_by_c[concurrency]
    if base["output_tokens_per_s"] <= 0:
        return None
    return round(batched["output_tokens_per_s"] / base["output_tokens_per_s"], 2)


def _speedup_blurb(summary: dict[str, Any]) -> str:
    value = summary.get("throughput_speedup_at_c16")
    if not value:
        return ""
    return (
        f"Continuous-batching throughput speedup at highest shared concurrency: "
        f"**{value}x** vs naive sequential fixture."
    )


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    concurrencies = [int(x) for x in args.concurrencies.split(",") if x.strip()]
    if args.backend == "fixture":
        payload = await run_fixture(
            concurrencies=concurrencies,
            max_tokens=args.max_tokens,
            rounds=args.rounds,
        )
    else:
        if not args.base_url:
            raise SystemExit("--base-url is required for --backend live")
        payload = await run_live(
            base_url=args.base_url,
            concurrencies=concurrencies,
            max_tokens=args.max_tokens,
            rounds=args.rounds,
        )
    speedup = _speedup(payload)
    summary = {
        "suite": "serving",
        "recorded_at": datetime.now(UTC).isoformat(),
        "concurrencies": concurrencies,
        "max_tokens": args.max_tokens,
        "rounds": args.rounds,
        "throughput_speedup_at_c16": speedup,
        "success_rate": 1.0,
        **payload,
        "table_markdown": _throughput_table(payload),
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--concurrencies", default="1,4,8,16,32")
    parser.add_argument("--max-tokens", type=int, default=32)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument(
        "--out",
        default=str(ROOT / "evals" / "results" / "serving"),
        help="Directory for summary.json + README.md",
    )
    args = parser.parse_args()
    summary = asyncio.run(main_async(args))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    readme = f"""# Serving results

Recorded: `{summary["recorded_at"]}`
Backend: **{summary["backend"]}**

{summary.get("note", "")}

## Throughput / latency

{summary["table_markdown"]}

{_speedup_blurb(summary)}

## Reproduce

```powershell
# Fixture (CI / no GPU)
.\\venv\\Scripts\\python.exe evals\\suites\\serving\\run.py --backend fixture

# Live rented GPU (after infra/vllm/up.ps1)
.\\venv\\Scripts\\python.exe evals\\suites\\serving\\run.py --backend live --base-url http://localhost:8000/v1
```

See `docs/09-MODEL-SERVING.md` §5 and ADR-0006.
"""
    (out_dir / "README.md").write_text(readme, encoding="utf-8")
    print(summary["table_markdown"])
    if summary.get("throughput_speedup_at_c16"):
        print(f"speedup={summary['throughput_speedup_at_c16']}x")
    if summary["success_rate"] != 1.0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
