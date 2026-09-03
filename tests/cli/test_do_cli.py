"""CLI task and tool commands: which endpoint, which payload, which exit code."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tests.cli.conftest import Install
from typer.testing import CliRunner

from astra.cli.main import app

runner = CliRunner()


def _task(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "01TASK",
        "instruction": "list docs",
        "status": "CREATED",
        "origin": "cli",
        "capability_ceiling": "L2",
        "plan_version": 0,
        "replan_count": 0,
        "tokens_used": 0,
        "cost_usd": "0",
        "created_at": "2026-08-29T00:00:00Z",
        "progress": {"steps_total": 0, "steps_done": 0, "actions_total": 0, "actions_done": 0},
    }
    return body | overrides


@pytest.mark.req("FR-101")
def test_do_posts_instruction_and_ceiling(recorder: Install) -> None:
    rec = recorder({("POST", "/v1/tasks"): _task(status="READY", plan_version=1)})

    result = runner.invoke(app, ["do", "list docs", "--ceiling", "L1"])

    assert result.exit_code == 0, result.output
    sent = rec.calls[0]["json"]
    assert sent["instruction"] == "list docs"
    assert sent["capability_ceiling"] == "L1"
    assert sent["origin"] == "cli"
    assert sent["dry_run"] is False
    assert "READY" in result.output


def test_do_attaches_a_plan_file(recorder: Install, tmp_path: Path) -> None:
    plan = {
        "steps": [
            {
                "alias": "survey",
                "title": "List",
                "intent": "See files",
                "actions": [{"alias": "ls", "tool": "fs.list_dir", "parameters": {"path": "D:/x"}}],
            }
        ]
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    rec = recorder({("POST", "/v1/tasks"): _task(status="READY", plan_version=1)})

    result = runner.invoke(app, ["do", "list docs", "--plan", str(path)])

    assert result.exit_code == 0, result.output
    assert rec.calls[0]["json"]["plan"] == plan
    assert "READY" in result.output


def test_do_rejects_malformed_plan_json(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    path.write_text("{not json}", encoding="utf-8")
    result = runner.invoke(app, ["do", "list docs", "--plan", str(path)])
    assert result.exit_code == 1
    assert "not valid JSON" in result.output


def test_do_dry_run_sends_the_flag(recorder: Install) -> None:
    rec = recorder({("POST", "/v1/tasks"): _task(status="PLANNING")})

    result = runner.invoke(app, ["do", "preview", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert rec.calls[0]["json"]["dry_run"] is True
    assert "not dispatched" in result.output


def test_do_watch_polls_until_the_task_settles(recorder: Install) -> None:
    rec = recorder(
        {
            ("POST", "/v1/tasks"): _task(status="READY", plan_version=1),
            ("GET", "/v1/tasks/01TASK"): _task(status="SUCCEEDED", plan_version=1),
        }
    )

    result = runner.invoke(app, ["do", "list docs", "--watch"])

    assert result.exit_code == 0, result.output
    assert rec.calls[0]["method"] == "POST"
    assert rec.calls[1]["method"] == "GET"
    assert rec.calls[1]["path"] == "/v1/tasks/01TASK"
    assert "SUCCEEDED" in result.output


def test_show_fetches_the_task_and_its_plan(recorder: Install) -> None:
    rec = recorder(
        {
            ("GET", "/v1/tasks/01TASK"): _task(
                status="READY",
                progress={
                    "steps_total": 1,
                    "steps_done": 0,
                    "actions_total": 1,
                    "actions_done": 0,
                },
            ),
            ("GET", "/v1/tasks/01TASK/plan"): {
                "task_id": "01TASK",
                "plan_version": 1,
                "steps": [],
                "actions": [
                    {
                        "id": "01ACTION",
                        "step_id": "01STEP",
                        "tool": "fs.list_dir",
                        "status": "PLANNED",
                        "capability_level": "L0",
                    }
                ],
            },
        }
    )

    result = runner.invoke(app, ["show", "01TASK"])

    assert result.exit_code == 0, result.output
    assert rec.calls[0]["path"] == "/v1/tasks/01TASK"
    assert rec.calls[1]["path"] == "/v1/tasks/01TASK/plan"
    assert "fs.list_dir" in result.output
    assert "PLANNED" in result.output


def test_tasks_passes_the_status_filter(recorder: Install) -> None:
    rec = recorder({("GET", "/v1/tasks"): {"items": [_task()]}})

    result = runner.invoke(app, ["tasks", "--status", "created", "--limit", "5"])

    assert result.exit_code == 0, result.output
    assert rec.calls[0]["params"] == {"limit": 5, "status": "CREATED"}
    assert "01TASK" in result.output


def test_tools_list_renders_the_catalog(recorder: Install) -> None:
    recorder(
        {
            ("GET", "/v1/tools"): {
                "items": [
                    {
                        "name": "fs.read_file",
                        "base_capability": "L0",
                        "reversible": False,
                        "idempotent": True,
                        "description": "Read a UTF-8 text file.",
                    }
                ]
            }
        }
    )

    result = runner.invoke(app, ["tools", "list"])

    assert result.exit_code == 0, result.output
    assert "fs.read_file" in result.output


def test_tools_show_prints_the_schema(recorder: Install) -> None:
    rec = recorder(
        {
            ("GET", "/v1/tools/fs.read_file"): {
                "name": "fs.read_file",
                "version": "1.0.0",
                "base_capability": "L0",
                "reversible": False,
                "idempotent": True,
                "description": "Read a file.",
                "input_schema": {"properties": {"path": {"type": "string"}}},
            }
        }
    )

    result = runner.invoke(app, ["tools", "show", "fs.read_file"])

    assert result.exit_code == 0, result.output
    assert rec.calls[0]["path"] == "/v1/tools/fs.read_file"
    assert "path" in result.output


def test_tools_invoke_posts_parameters(recorder: Install) -> None:
    rec = recorder(
        {
            ("POST", "/v1/tools/task.report/invoke"): {
                "invoke_id": "01INV",
                "tool": "task.report",
                "capability_level": "L0",
                "decision": "ALLOW",
                "result": {"summary": "hi", "findings": []},
            }
        }
    )

    result = runner.invoke(app, ["tools", "invoke", "task.report", "--json", '{"summary": "hi"}'])

    assert result.exit_code == 0, result.output
    assert rec.calls[0]["json"] == {"parameters": {"summary": "hi"}}
    assert "ALLOW" in result.output
    assert "hi" in result.output


def test_tools_invoke_rejects_malformed_json() -> None:
    result = runner.invoke(app, ["tools", "invoke", "task.report", "--json", "{nope}"])
    assert result.exit_code == 1
    assert "not valid JSON" in result.output
