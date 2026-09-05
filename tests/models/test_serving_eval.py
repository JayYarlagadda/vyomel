"""Serving eval harness (FR-707)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from evals.suites.serving.run import main_async


class _Args:
    backend = "fixture"
    base_url = ""
    concurrencies = "1,4,8"
    max_tokens = 16
    rounds = 1


@pytest.mark.asyncio
@pytest.mark.req("FR-707")
async def test_fixture_serving_shows_batching_speedup(tmp_path: Path) -> None:
    summary = await main_async(_Args())  # type: ignore[arg-type]
    assert summary["backend"] == "fixture"
    assert summary["success_rate"] == 1.0
    labels = {s["label"] for s in summary["systems"]}
    assert labels == {"baseline", "vllm"}
    # Continuous batching must beat naive serialization at the highest concurrency.
    base = next(r for r in summary["systems"][0]["rows"] if r["concurrency"] == 8)
    batched = next(r for r in summary["systems"][1]["rows"] if r["concurrency"] == 8)
    assert batched["requests_per_s"] > base["requests_per_s"]
    assert "table_markdown" in summary
    out = tmp_path / "summary.json"
    out.write_text(json.dumps(summary), encoding="utf-8")
    assert out.is_file()
