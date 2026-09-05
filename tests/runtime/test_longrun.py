"""Long-run durability under chaos (M6, FR-208)."""

from __future__ import annotations

import pytest

from vyomel.core.config import Settings
from vyomel.core.types import TaskStatus
from vyomel.orchestrator.longrun import HarnessConfig, LongrunHarness
from vyomel.runtime.scheduler import Scheduler
from vyomel.runtime.worker import Worker


@pytest.mark.integration
@pytest.mark.req("FR-208")
async def test_research_task_survives_simulated_worker_kills(
    runtime_db: Settings,
    queue,
    scheduler: Scheduler,
    worker: Worker,
) -> None:
    config = HarnessConfig(items=8, duration_s=30.0, kill_interval_s=0.0)
    harness = LongrunHarness(runtime_db, queue, scheduler=scheduler, worker=worker)
    task = await harness.install(items=config.items)
    result = await harness.run(task.id, expected_fetches=config.items, config=config)
    assert result.simulated_kills >= 1
    assert result.audit.ok
    assert result.audit.fetch_succeeded == config.items
    assert result.audit.task_status is TaskStatus.SUCCEEDED


@pytest.mark.integration
@pytest.mark.req("FR-208")
async def test_research_task_survives_redis_stream_flush(
    runtime_db: Settings,
    queue,
    scheduler: Scheduler,
    worker: Worker,
) -> None:
    config = HarnessConfig(items=6, duration_s=30.0, kill_interval_s=None)
    harness = LongrunHarness(runtime_db, queue, scheduler=scheduler, worker=worker)
    task = await harness.install(items=config.items)
    await scheduler.tick()
    await worker.run_once(block_ms=50)
    await harness.flush_redis_stream()
    await harness.recover()
    result = await harness.run(task.id, expected_fetches=config.items, config=config)
    assert result.audit.ok
    assert result.audit.fetch_succeeded == config.items
