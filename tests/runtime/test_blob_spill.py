"""Large action results spill to the blob store (FR-208, docs/07 §9)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from tests.runtime.helpers import drain, install_plan
from vyomel.core.config import Settings
from vyomel.core.types import ActionStatus
from vyomel.orchestrator.plans import ActionSpec, HandwrittenPlan, StepSpec
from vyomel.orchestrator.runtime import make_scheduler, make_worker
from vyomel.runtime.queue import ActionQueue
from vyomel.store.blobs import is_blob_ref, resolve_result
from vyomel.store.db import session_scope
from vyomel.store.models import Action, Task


@pytest.mark.integration
@pytest.mark.req("FR-208")
async def test_large_tool_result_is_spilled_and_task_report_resolves(
    runtime_db: Settings,
    queue: ActionQueue,
) -> None:
    settings = runtime_db.model_copy(update={"blob_spill_threshold_bytes": 512})
    scheduler = make_scheduler(settings, queue)
    worker = make_worker(settings, queue, worker_id="blob-worker")
    plan = HandwrittenPlan(
        steps=[
            StepSpec(
                alias="big",
                title="Large payload",
                intent="spill",
                actions=[
                    ActionSpec(
                        alias="lp",
                        tool="test.large_payload",
                        parameters={"size_bytes": 8_000},
                    )
                ],
            ),
            StepSpec(
                alias="done",
                title="Report",
                intent="finish",
                actions=[
                    ActionSpec(
                        alias="r",
                        tool="task.report",
                        parameters={"summary": "large payload task done"},
                    )
                ],
                depends_on=["big"],
            ),
        ]
    )
    task = await install_plan(settings, plan)
    await drain(scheduler, worker)

    async with session_scope() as session:
        actions = list(
            (await session.execute(select(Action).where(Action.task_id == task.id))).scalars()
        )
        large = next(a for a in actions if a.tool == "test.large_payload")
        assert large.status is ActionStatus.SUCCEEDED
        assert large.result is not None
        assert is_blob_ref(large.result)
        resolved = resolve_result(large.result, blob_dir=settings.blob_dir)
        assert resolved is not None
        assert len(resolved["payload"]) == 8_000

        finished = await session.get(Task, task.id)
        assert finished is not None
        assert finished.status.value == "SUCCEEDED"
        assert finished.result is not None
        assert finished.result.get("summary") == "large payload task done"
