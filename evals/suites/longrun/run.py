"""Long-run durability eval (docs/11-EVALUATION.md §8, M6 exit criteria)."""

from __future__ import annotations

import argparse
import asyncio
import json

from vyomel.core.config import Settings
from vyomel.orchestrator.longrun import MODES, LongrunHarness
from vyomel.orchestrator.runtime import make_queue
from vyomel.store.db import dispose_engine, init_engine


async def run_mode(settings: Settings, mode: str) -> dict[str, object]:
    config = MODES[mode]
    init_engine(settings)
    settings.ensure_directories()
    redis, queue = make_queue(settings)
    harness = LongrunHarness(settings, queue)
    try:
        task = await harness.install(items=config.items)
        result = await harness.run(
            task.id,
            expected_fetches=config.items,
            config=config,
        )
        audit = result.audit
        return {
            "mode": mode,
            "items": config.items,
            "task_id": task.id,
            "task_status": audit.task_status.value,
            "fetch_succeeded": audit.fetch_succeeded,
            "lost_fetches": audit.lost_fetches,
            "duplicate_fetches": audit.duplicate_fetches,
            "duplicate_idempotency_keys": audit.duplicate_idempotency_keys,
            "simulated_kills": result.simulated_kills,
            "elapsed_s": round(result.elapsed_s, 3),
            "ok": audit.ok,
        }
    finally:
        await redis.aclose()
        await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run long-run durability eval.")
    parser.add_argument(
        "--mode",
        choices=tuple(MODES),
        default="fast",
        help="fast=CI (~10 items), standard=100 items, chaos=20 min / 60s kills",
    )
    args = parser.parse_args()
    settings = Settings(env="test", log_format="json")
    metrics = asyncio.run(run_mode(settings, args.mode))
    print(json.dumps(metrics, indent=2))
    if not metrics["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
