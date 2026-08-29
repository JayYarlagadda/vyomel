"""Astra CLI.

The CLI is a client of the HTTP API, not a second entry point into the domain.
Keeping it that way guarantees the API stays complete enough to build any other
client on -- desktop UI, voice, or a wearable.

``doctor`` and ``db`` are the exceptions: they are local operational commands
that must work before the API can start.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from astra import __version__
from astra.core.config import get_settings

app = typer.Typer(
    name="astra",
    help="Personal AI execution platform.",
    no_args_is_help=True,
    add_completion=False,
)
db_app = typer.Typer(help="Database operations.", no_args_is_help=True)
app.add_typer(db_app, name="db")
audit_app = typer.Typer(help="Audit trail.", no_args_is_help=True)
app.add_typer(audit_app, name="audit")
policy_app = typer.Typer(help="Policy inspection.", no_args_is_help=True)
app.add_typer(policy_app, name="policy")

console = Console()


@app.command()
def version() -> None:
    """Print the Astra version."""
    console.print(f"astra {__version__}")


@app.command()
def serve(
    host: Annotated[str | None, typer.Option(help="Bind address.")] = None,
    port: Annotated[int | None, typer.Option(help="Bind port.")] = None,
    reload: Annotated[bool, typer.Option(help="Auto-reload on code change.")] = False,
) -> None:
    """Run the API server."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "astra.api.app:app",
        host=host or settings.api_host,
        port=port or settings.api_port,
        reload=reload,
        log_config=None,
    )


@app.command()
def worker() -> None:
    """Run an action worker process."""
    from astra.orchestrator.runtime import run_worker

    settings = get_settings()
    asyncio.run(run_worker(settings))


@app.command()
def doctor() -> None:
    """Verify the local environment against docs/13-ENVIRONMENT.md."""
    from astra.cli.doctor import run_doctor

    raise typer.Exit(code=0 if asyncio.run(run_doctor(console)) else 1)


@app.command()
def approvals(
    status: Annotated[str, typer.Option(help="Filter by approval status.")] = "PENDING",
    task: Annotated[str | None, typer.Option(help="Filter by task id.")] = None,
    limit: Annotated[int, typer.Option(help="Maximum rows.")] = 20,
) -> None:
    """List actions waiting for a decision."""
    from astra.cli.client import request

    params: dict[str, object] = {"limit": limit}
    if status.upper() != "ALL":
        params["status"] = status.upper()
    if task:
        params["task_id"] = task

    items = request(console, "GET", "/v1/approvals", params=params)["items"]
    if not items:
        console.print("[dim]Nothing is waiting for you.[/dim]")
        return

    table = Table("id", "level", "status", "summary", "expires")
    for item in items:
        table.add_row(
            item["id"],
            item["capability_level"],
            item["status"],
            item["summary"],
            item["expires_at"],
        )
    console.print(table)


@app.command()
def approve(
    approval_id: Annotated[str, typer.Argument(help="Approval id.")],
    by: Annotated[str, typer.Option(help="Who is deciding.")] = "user:cli",
) -> None:
    """Approve an action exactly as presented."""
    _decide({"decision": "APPROVED", "decided_by": by}, approval_id)


@app.command()
def reject(
    approval_id: Annotated[str, typer.Argument(help="Approval id.")],
    reason: Annotated[str | None, typer.Option(help="Why.")] = None,
    by: Annotated[str, typer.Option(help="Who is deciding.")] = "user:cli",
) -> None:
    """Refuse an action. The action fails; it is not retried."""
    payload: dict[str, object] = {"decision": "REJECTED", "decided_by": by}
    if reason:
        payload["reason"] = reason
    _decide(payload, approval_id)


@app.command()
def modify(
    approval_id: Annotated[str, typer.Argument(help="Approval id.")],
    set_: Annotated[
        list[str] | None,
        typer.Option("--set", help="key=value override; repeatable. Values parse as JSON."),
    ] = None,
    by: Annotated[str, typer.Option(help="Who is deciding.")] = "user:cli",
) -> None:
    """Approve with edited parameters.

    The edit is re-validated against the tool schema and re-classified. If it
    raises the capability level, this does not approve anything -- Astra asks
    again, showing what the edited action actually does.
    """
    from astra.cli.client import request

    if not set_:
        console.print("[red]--set key=value is required.[/red]")
        raise typer.Exit(code=1)

    approval = request(console, "GET", f"/v1/approvals/{approval_id}")
    parameters = dict(approval.get("presented", {}).get("parameters", {}))
    for override in set_:
        key, separator, raw = override.partition("=")
        if not separator:
            console.print(f"[red]--set expects key=value, got {override!r}.[/red]")
            raise typer.Exit(code=1)
        parameters[key.strip()] = _coerce(raw)

    _decide(
        {"decision": "MODIFIED", "decided_by": by, "parameters": parameters},
        approval_id,
    )


def _coerce(raw: str) -> object:
    """JSON if it parses, otherwise the literal string.

    ``--set value=85`` should be the number 85 and ``--set body=done`` should be
    the string "done", which is exactly the distinction JSON already makes.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _decide(payload: dict[str, object], approval_id: str) -> None:
    from astra.cli.client import request

    body = request(console, "POST", f"/v1/approvals/{approval_id}/decide", json=payload)
    console.print(f"Approval [bold]{body['id']}[/bold] is now [bold]{body['status']}[/bold].")


@app.command()
def cancel(
    task_id: Annotated[str, typer.Argument(help="Task id.")],
    compensate: Annotated[
        bool,
        typer.Option(help="Undo reversible SUCCEEDED actions in reverse topological order."),
    ] = True,
) -> None:
    """Cancel a task. Reversible completed actions are compensated by default."""
    from astra.cli.client import request

    body = request(console, "POST", f"/v1/tasks/{task_id}/cancel", json={"compensate": compensate})
    console.print(f"Task [bold]{body['task_id']}[/bold] is now [bold]{body['status']}[/bold].")
    irreversible = body.get("irreversible") or []
    if irreversible:
        console.print("[yellow]Could not undo:[/yellow]")
        for item in irreversible:
            console.print(f"  {item['tool']}: {item['summary']}")
    still = body.get("still_running") or []
    if still:
        console.print(f"[dim]{len(still)} action(s) still running.[/dim]")


@audit_app.command("tail")
def audit_tail(
    task: Annotated[str | None, typer.Option(help="Filter by task id.")] = None,
    event: Annotated[str | None, typer.Option(help="Filter by event type.")] = None,
    limit: Annotated[int, typer.Option(help="Maximum rows.")] = 20,
) -> None:
    """Show the most recent audit records."""
    from astra.cli.client import request

    params: dict[str, object] = {"limit": limit}
    if task:
        params["task_id"] = task
    if event:
        params["event_type"] = event

    items = request(console, "GET", "/v1/audit", params=params)["items"]
    table = Table("id", "occurred", "actor", "event", "level", "task")
    for item in reversed(items):
        table.add_row(
            str(item["id"]),
            item["occurred_at"],
            item["actor"],
            item["event_type"],
            item["capability_level"] or "",
            item["task_id"] or "",
        )
    console.print(table)


@audit_app.command("verify")
def audit_verify(
    start_id: Annotated[int | None, typer.Option(help="Verify from this record onward.")] = None,
) -> None:
    """Walk the hash chain and report the first divergence."""
    from astra.cli.client import request

    params = {"start_id": start_id} if start_id else None
    report = request(console, "POST", "/v1/audit/verify", params=params)
    if report["ok"]:
        console.print(f"[green]Chain intact[/green] across {report['rows']} records.")
        return
    console.print(
        f"[red]Chain broken[/red] at record {report['first_divergence_id']}: {report['detail']}"
    )
    raise typer.Exit(code=1)


@policy_app.command("show")
def policy_show() -> None:
    """Show the active policy and its version hash."""
    _print_policy(_policy_call("GET", "/v1/policy"))


@policy_app.command("reload")
def policy_reload() -> None:
    """Re-read the policy file from disk."""
    _print_policy(_policy_call("POST", "/v1/policy/reload"))


@policy_app.command("test")
def policy_test(
    tool: Annotated[str, typer.Argument(help="Tool name, e.g. fs.write_file.")],
    parameters: Annotated[str, typer.Argument(help="Parameters as a JSON object.")] = "{}",
) -> None:
    """Ask what Astra would do with an invocation, without running it."""
    from astra.cli.client import request

    try:
        parsed = json.loads(parameters)
    except json.JSONDecodeError as exc:
        console.print(f"[red]Parameters are not valid JSON:[/red] {exc}")
        raise typer.Exit(code=1) from None

    body = request(console, "POST", "/v1/policy/test", json={"tool": tool, "parameters": parsed})
    colour = {"ALLOW": "green", "CONFIRM": "yellow", "DENY": "red"}.get(body["decision"], "white")
    table = Table("field", "value")
    table.add_row("tool", body["tool"])
    table.add_row("capability", body["capability_level"])
    table.add_row("decision", f"[{colour}]{body['decision']}[/{colour}]")
    table.add_row("rule", body["rule_id"])
    table.add_row("reason", body["reason"])
    if body["escalation_reasons"]:
        table.add_row("escalated because", "\n".join(body["escalation_reasons"]))
    table.add_row("policy", f"v{body['policy_version']} {body['policy_hash'][:12]}")
    console.print(table)


def _policy_call(method: str, path: str) -> dict[str, object]:
    from astra.cli.client import request

    body: dict[str, object] = request(console, method, path)
    return body


def _print_policy(body: dict[str, object]) -> None:
    console.print(
        f"policy v{body['version']} [dim]{body['policy_hash']}[/dim] "
        f"from {body.get('source') or 'unknown source'}"
    )
    defaults = body["defaults"]
    assert isinstance(defaults, dict)
    console.print("defaults: " + ", ".join(f"{k}={v}" for k, v in sorted(defaults.items())))

    rules = body["rules"]
    assert isinstance(rules, list)
    table = Table("rule", "decision", "tool", "level", "reason")
    for rule in rules:
        table.add_row(
            rule["id"],
            rule["decision"],
            rule["tool"] or "*",
            rule["max_level"] or rule["level"] or "*",
            rule["reason"] or "",
        )
    console.print(table)


@db_app.command("upgrade")
def db_upgrade(revision: Annotated[str, typer.Argument()] = "head") -> None:
    """Apply migrations."""
    _alembic("upgrade", revision)


@db_app.command("downgrade")
def db_downgrade(revision: Annotated[str, typer.Argument()] = "-1") -> None:
    """Revert migrations."""
    _alembic("downgrade", revision)


@db_app.command("current")
def db_current() -> None:
    """Show the applied schema revision."""
    _alembic("current")


@db_app.command("check")
def db_check() -> None:
    """Verify connectivity and that the schema is at head."""

    async def _check() -> bool:
        from sqlalchemy import text

        from astra.store.db import dispose_engine, get_engine, init_engine

        settings = get_settings()
        init_engine(settings)
        table = Table("check", "result")
        ok = True
        try:
            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
                table.add_row("connectivity", "[green]ok[/green]")

                result = await conn.execute(text("SELECT version_num FROM alembic_version"))
                row = result.first()
                if row:
                    table.add_row("schema revision", f"[green]{row[0]}[/green]")
                else:
                    table.add_row("schema revision", "[red]no migrations applied[/red]")
                    ok = False

                result = await conn.execute(
                    text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
                )
                if result.first():
                    table.add_row("pgvector", "[green]installed[/green]")
                else:
                    table.add_row("pgvector", "[red]missing[/red]")
                    ok = False
        except Exception as exc:
            table.add_row("connectivity", f"[red]{exc}[/red]")
            ok = False
        finally:
            await dispose_engine()
        console.print(table)
        return ok

    raise typer.Exit(code=0 if asyncio.run(_check()) else 1)


def _alembic(*args: str) -> None:
    executable = shutil.which("alembic") or f"{sys.executable} -m alembic"
    command = [*executable.split(), *args]
    raise typer.Exit(code=subprocess.run(command, check=False).returncode)  # noqa: S603


if __name__ == "__main__":
    app()
