"""Log correlation ids (FR-803)."""

from __future__ import annotations

import json

import pytest

from vyomel.core.config import Settings
from vyomel.core.logging import bind_task_context, clear_task_context, configure_logging, get_logger


@pytest.mark.req("FR-803")
def test_bound_ids_appear_on_every_record(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(Settings(env="test", log_format="json"))
    clear_task_context()
    bind_task_context(
        task_id="task-1",
        step_id="step-1",
        action_id="action-1",
        trace_id="a" * 32,
        span_id="b" * 16,
    )
    get_logger("vyomel.obs.test").info("vyomel.obs.probe", extra="ok")
    err = capsys.readouterr().err.strip().splitlines()[-1]
    payload = json.loads(err)
    assert payload["task_id"] == "task-1"
    assert payload["step_id"] == "step-1"
    assert payload["action_id"] == "action-1"
    assert payload["trace_id"] == "a" * 32
    assert payload["span_id"] == "b" * 16
    assert payload["event"] == "vyomel.obs.probe"
    clear_task_context()
    get_logger("vyomel.obs.test").info("vyomel.obs.cleared")
    cleared = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert "task_id" not in cleared
