"""Vyomel CLI.

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
import time
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from vyomel import __version__
from vyomel.core.config import get_settings

app = typer.Typer(
    name="vyomel",
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
tools_app = typer.Typer(help="Tool catalog and debug invoke.", no_args_is_help=True)
app.add_typer(tools_app, name="tools")
memory_app = typer.Typer(help="Ingest documents and query semantic memory.", no_args_is_help=True)
app.add_typer(memory_app, name="memory")
auth_app = typer.Typer(help="OAuth tokens (keyring / local store).", no_args_is_help=True)
app.add_typer(auth_app, name="auth")
workflows_app = typer.Typer(help="Learned / saved workflows.", no_args_is_help=True)
app.add_typer(workflows_app, name="workflows")
voice_app = typer.Typer(help="Local voice STT/TTS session.", no_args_is_help=True)
app.add_typer(voice_app, name="voice")

console = Console()


@auth_app.command("login")
def auth_login(
    provider: Annotated[str, typer.Argument(help="google or github")],
    account: Annotated[str, typer.Option(help="Account label.")] = "default",
) -> None:
    """Issue a least-privilege token for the fixture backend (or store a live grant)."""
    from vyomel.core.oauth import PROVIDER_SCOPES, get_token_store, issue_token

    settings = get_settings()
    allowed = PROVIDER_SCOPES.get(provider)
    if allowed is None:
        console.print(f"[red]unknown provider {provider!r}[/red]")
        raise typer.Exit(code=1)
    store = get_token_store(backend=settings.oauth_backend, workspace_root=settings.workspace_root)
    token = issue_token(provider, account, set(allowed))
    store.put(token)
    console.print(f"logged in {provider}:{account} scopes={sorted(token.scopes)}")


@auth_app.command("status")
def auth_status() -> None:
    """List stored OAuth accounts. Does not print tokens."""
    from vyomel.core.oauth import get_token_store

    settings = get_settings()
    store = get_token_store(backend=settings.oauth_backend, workspace_root=settings.workspace_root)
    accounts = store.list_accounts()
    if not accounts:
        console.print("[dim]No OAuth accounts stored.[/dim]")
        return
    table = Table("provider", "account")
    for provider, account in accounts:
        table.add_row(provider, account)
    console.print(table)


@auth_app.command("revoke")
def auth_revoke(
    provider: Annotated[str, typer.Argument(help="google or github")],
    account: Annotated[str, typer.Option(help="Account label.")] = "default",
) -> None:
    """Delete a stored OAuth grant."""
    from vyomel.core.oauth import get_token_store

    settings = get_settings()
    store = get_token_store(backend=settings.oauth_backend, workspace_root=settings.workspace_root)
    if store.delete(provider, account):
        console.print(f"revoked {provider}:{account}")
        return
    console.print(f"[red]no token for {provider}:{account}[/red]")
    raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the Vyomel version."""
    console.print(f"vyomel {__version__}")


@app.command()
def trace(task_id: Annotated[str, typer.Argument(help="Task id.")]) -> None:
    """Render the persisted task timeline (FR-804)."""
    from vyomel.cli.client import request

    body = request(console, "GET", f"/v1/tasks/{task_id}/trace")
    console.print(body["rendered"])


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
        "vyomel.api.app:app",
        host=host or settings.api_host,
        port=port or settings.api_port,
        reload=reload,
        log_config=None,
    )


@app.command()
def worker() -> None:
    """Run an action worker process."""
    from vyomel.orchestrator.runtime import run_worker

    settings = get_settings()
    asyncio.run(run_worker(settings))


@app.command()
def scheduler() -> None:
    """Run the scheduler with Redis leader election (M13)."""
    from vyomel.orchestrator.runtime import run_scheduler

    settings = get_settings()
    asyncio.run(run_scheduler(settings))


@app.command()
def agent(
    api_base: Annotated[
        str, typer.Option(help="Control-plane API base URL.")
    ] = "http://127.0.0.1:8080",
    agent_id: Annotated[str, typer.Option(help="Agent identity.")] = "local",
    ceiling: Annotated[str, typer.Option(help="Local capability ceiling.")] = "L2",
) -> None:
    """Run the host-bound local agent (desktop tools; ADR-0009)."""
    from vyomel.core.types import Capability
    from vyomel.orchestrator.agent_process import run_local_agent

    settings = get_settings()
    asyncio.run(
        run_local_agent(
            settings,
            api_base=api_base,
            agent_id=agent_id,
            ceiling=Capability(ceiling),
        )
    )


@app.command()
def doctor() -> None:
    """Verify the local environment against docs/13-ENVIRONMENT.md."""
    from vyomel.cli.doctor import run_doctor

    raise typer.Exit(code=0 if asyncio.run(run_doctor(console)) else 1)


@app.command()
def approvals(
    status: Annotated[str, typer.Option(help="Filter by approval status.")] = "PENDING",
    task: Annotated[str | None, typer.Option(help="Filter by task id.")] = None,
    limit: Annotated[int, typer.Option(help="Maximum rows.")] = 20,
) -> None:
    """List actions waiting for a decision."""
    from vyomel.cli.client import request

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
    raises the capability level, this does not approve anything -- Vyomel asks
    again, showing what the edited action actually does.
    """
    from vyomel.cli.client import request

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
    from vyomel.cli.client import request

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
    from vyomel.cli.client import request

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


@app.command()
def do(
    instruction: Annotated[str, typer.Argument(help="What you want Vyomel to do.")],
    ceiling: Annotated[str, typer.Option(help="Capability ceiling (L0-L4).")] = "L2",
    dry_run: Annotated[bool, typer.Option(help="Install a plan without dispatching it.")] = False,
    plan: Annotated[
        Path | None,
        typer.Option(help="Handwritten plan JSON (bypasses the planner)."),
    ] = None,
    watch: Annotated[bool, typer.Option(help="Poll until the task settles or waits for you.")] = (
        False
    ),
    interval: Annotated[float, typer.Option(help="Watch poll interval, seconds.")] = 1.0,
) -> None:
    """Create a task. The M5 planner decomposes the instruction unless --plan is given."""
    from vyomel.cli.client import request

    payload: dict[str, object] = {
        "instruction": instruction,
        "capability_ceiling": ceiling.upper(),
        "dry_run": dry_run,
        "origin": "cli",
    }
    if plan is not None:
        try:
            parsed = json.loads(plan.read_text(encoding="utf-8"))
        except FileNotFoundError:
            console.print(f"[red]Plan file not found:[/red] {plan}")
            raise typer.Exit(code=1) from None
        except json.JSONDecodeError as exc:
            console.print(f"[red]Plan is not valid JSON:[/red] {exc}")
            raise typer.Exit(code=1) from None
        payload["plan"] = parsed

    body = request(console, "POST", "/v1/tasks", json=payload)
    _print_task_line(body)
    status = str(body["status"])
    if status == "PLANNING" and dry_run:
        console.print("[dim]dry-run: plan installed and classified; not dispatched.[/dim]")
    if watch and status not in {"CREATED", "PLANNING"}:
        _watch_task(body["id"], interval=interval)


@app.command()
def tasks(
    status: Annotated[str | None, typer.Option(help="Filter by task status.")] = None,
    limit: Annotated[int, typer.Option(help="Maximum rows.")] = 20,
) -> None:
    """List recent tasks."""
    from vyomel.cli.client import request

    params: dict[str, object] = {"limit": limit}
    if status:
        params["status"] = status.upper()
    items = request(console, "GET", "/v1/tasks", params=params)["items"]
    if not items:
        console.print("[dim]No tasks.[/dim]")
        return
    table = Table("id", "status", "ceiling", "instruction")
    for item in items:
        instruction = item["instruction"]
        if len(instruction) > 60:
            instruction = instruction[:57] + "..."
        table.add_row(item["id"], item["status"], item["capability_ceiling"], instruction)
    console.print(table)


@app.command()
def show(task_id: Annotated[str, typer.Argument(help="Task id.")]) -> None:
    """Show a task, its plan, and current action statuses."""
    from vyomel.cli.client import request

    task = request(console, "GET", f"/v1/tasks/{task_id}")
    _print_task_line(task)
    progress = task.get("progress") or {}
    console.print(
        f"progress: {progress.get('steps_done', 0)}/{progress.get('steps_total', 0)} steps, "
        f"{progress.get('actions_done', 0)}/{progress.get('actions_total', 0)} actions"
    )
    plan = request(console, "GET", f"/v1/tasks/{task_id}/plan")
    actions = plan.get("actions") or []
    if not actions:
        console.print("[dim]No plan installed.[/dim]")
        return
    table = Table("action", "tool", "level", "status")
    for action in actions:
        table.add_row(action["id"], action["tool"], action["capability_level"], action["status"])
    console.print(table)


@tools_app.command("list")
def tools_list() -> None:
    """List registered tools."""
    from vyomel.cli.client import request

    items = request(console, "GET", "/v1/tools")["items"]
    table = Table("name", "level", "rev", "idem", "description")
    for item in items:
        table.add_row(
            item["name"],
            item["base_capability"],
            "yes" if item["reversible"] else "no",
            "yes" if item["idempotent"] else "no",
            item["description"].split(".")[0],
        )
    console.print(table)


@tools_app.command("show")
def tools_show(name: Annotated[str, typer.Argument(help="Tool name, e.g. fs.write_file.")]) -> None:
    """Show one tool's contract and input schema."""
    from vyomel.cli.client import request

    body = request(console, "GET", f"/v1/tools/{name}")
    table = Table("field", "value")
    table.add_row("name", body["name"])
    table.add_row("version", body["version"])
    table.add_row("capability", body["base_capability"])
    table.add_row("reversible", str(body["reversible"]))
    table.add_row("idempotent", str(body["idempotent"]))
    table.add_row("description", body["description"])
    console.print(table)
    console.print(json.dumps(body["input_schema"], indent=2))


@tools_app.command("invoke")
def tools_invoke(
    name: Annotated[str, typer.Argument(help="Tool name.")],
    json_: Annotated[
        str,
        typer.Option("--json", help="Parameters as a JSON object."),
    ] = "{}",
) -> None:
    """Run one tool now. Policy-gated; CONFIRM/DENY are refused, not auto-approved."""
    from vyomel.cli.client import request

    try:
        parameters = json.loads(json_)
    except json.JSONDecodeError as exc:
        console.print(f"[red]Parameters are not valid JSON:[/red] {exc}")
        raise typer.Exit(code=1) from None
    if not isinstance(parameters, dict):
        console.print("[red]--json must be a JSON object.[/red]")
        raise typer.Exit(code=1)

    body = request(console, "POST", f"/v1/tools/{name}/invoke", json={"parameters": parameters})
    console.print(f"{body['tool']} [{body['capability_level']}] {body['decision']}")
    console.print(json.dumps(body["result"], indent=2))


@memory_app.command("ingest")
def memory_ingest(
    paths: Annotated[list[Path], typer.Argument(help="Files or directories to ingest.")],
    recursive: Annotated[
        bool, typer.Option("--recursive", help="Walk directories for .md and .txt.")
    ] = False,
) -> None:
    """Chunk and index local markdown/text (synchronous)."""
    from vyomel.cli.client import request

    body = request(
        console,
        "POST",
        "/v1/memory/ingest",
        json={"paths": [str(path) for path in paths], "recursive": recursive, "watch": False},
    )
    table = Table("status", "chunks", "ver", "path")
    for item in body["documents"]:
        table.add_row(
            item["status"],
            str(item["chunk_count"]),
            str(item["version"]),
            item["path"],
        )
    console.print(table)


@memory_app.command("query")
def memory_query(
    query: Annotated[str, typer.Argument(help="Natural-language or identifier query.")],
    k: Annotated[int, typer.Option("--k", help="How many chunks to return.")] = 10,
    strategy: Annotated[
        str, typer.Option("--strategy", help="hybrid, vector, or lexical.")
    ] = "hybrid",
) -> None:
    """Hybrid retrieval with citations."""
    from vyomel.cli.client import request

    body = request(
        console,
        "POST",
        "/v1/memory/query",
        json={"query": query, "k": k, "strategy": strategy},
    )
    console.print(f"strategy={body['strategy']}  latency_ms={body['latency_ms']:.1f}")
    for hit in body["results"]:
        citation = hit["citation"]
        heading = " / ".join(citation["heading_path"]) if citation["heading_path"] else "(root)"
        console.print(
            f"[bold]{hit['score']:.4f}[/bold]  {citation['path']}  {heading}  "
            f"[{citation['char_start']}:{citation['char_end']}]"
        )
        console.print(hit["content"][:400])
        console.print()


@memory_app.command("show")
def memory_show(
    entity_id: Annotated[str, typer.Argument(help="Entity id from ingest or GET /entities.")],
) -> None:
    """Show a context-graph entity and linked documents."""
    from vyomel.cli.client import request

    body = request(console, "GET", f"/v1/memory/entities/{entity_id}")
    console.print(f"[bold]{body['type']}[/bold] {body['name']}  salience={body['salience']:.2f}")
    for document in body["documents"]:
        console.print(f"  doc {document['path']}  chunks={document['chunk_count']}")


@memory_app.command("forget")
def memory_forget(
    entity_id: Annotated[str, typer.Argument(help="Entity to hard-delete with its chunks.")],
) -> None:
    """Hard-delete an entity, its documents, chunks, and relations (FR-509)."""
    from vyomel.cli.client import request

    body = request(console, "DELETE", f"/v1/memory/entities/{entity_id}")
    console.print(
        f"forgot {body['entity_id']}: "
        f"{body['documents_deleted']} docs, {body['chunks_deleted']} chunks, "
        f"{body['relations_deleted']} relations, {body.get('episodes_deleted', 0)} episodes"
    )


@memory_app.command("remember")
def memory_remember(
    name: Annotated[str, typer.Argument(help="Canonical entity name.")],
    entity_type: Annotated[
        str, typer.Option("--type", help="Entity type enum value.")
    ] = "preference",
    alias: Annotated[list[str] | None, typer.Option("--alias", help="Additional aliases.")] = None,
) -> None:
    """Persist an explicit fact into the context graph."""
    from vyomel.cli.client import request

    body = request(
        console,
        "POST",
        "/v1/memory/remember",
        json={
            "type": entity_type,
            "name": name,
            "aliases": alias or [],
            "attributes": {},
        },
    )
    console.print(f"remembered {body['type']} {body['name']} -> {body['entity_id']}")


@memory_app.command("episodes")
def memory_episodes(
    entity_id: Annotated[str | None, typer.Option("--entity", help="Filter by entity id.")] = None,
    limit: Annotated[int, typer.Option("--limit", help="Max rows.")] = 20,
) -> None:
    """List episodic memory records."""
    from vyomel.cli.client import request

    params: dict[str, str | int] = {"limit": limit}
    if entity_id:
        params["entity_id"] = entity_id
    body = request(console, "GET", "/v1/memory/episodes", params=params)
    for item in body["items"]:
        console.print(f"{item['finished_at']}  {item['outcome']}  {item['summary'][:120]}")


def _print_task_line(body: dict[str, object]) -> None:
    console.print(
        f"Task [bold]{body['id']}[/bold] is [bold]{body['status']}[/bold] "
        f"(ceiling {body['capability_ceiling']})"
    )


def _watch_task(task_id: str, *, interval: float) -> None:
    from vyomel.cli.client import request

    stop = {"SUCCEEDED", "FAILED", "CANCELLED", "WAITING_FOR_USER", "NEEDS_HUMAN", "PLANNING"}
    while True:
        body = request(console, "GET", f"/v1/tasks/{task_id}")
        status = str(body["status"])
        progress = body.get("progress") or {}
        console.print(
            f"  {status}  "
            f"{progress.get('actions_done', 0)}/{progress.get('actions_total', 0)} actions"
        )
        if status in stop:
            return
        time.sleep(max(interval, 0.1))


@audit_app.command("tail")
def audit_tail(
    task: Annotated[str | None, typer.Option(help="Filter by task id.")] = None,
    event: Annotated[str | None, typer.Option(help="Filter by event type.")] = None,
    limit: Annotated[int, typer.Option(help="Maximum rows.")] = 20,
) -> None:
    """Show the most recent audit records."""
    from vyomel.cli.client import request

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
    from vyomel.cli.client import request

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
    """Ask what Vyomel would do with an invocation, without running it."""
    from vyomel.cli.client import request

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
    from vyomel.cli.client import request

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

        from vyomel.store.db import dispose_engine, get_engine, init_engine

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


@workflows_app.command("list")
def workflows_list(
    status: Annotated[str | None, typer.Option(help="proposed|accepted|rejected")] = None,
) -> None:
    """List learned / saved workflows."""
    from vyomel.cli.client import request

    params = {"status": status} if status else None
    items = request(console, "GET", "/v1/workflows", params=params)["items"]
    if not items:
        console.print("[dim]No workflows.[/dim]")
        return
    table = Table("id", "name", "status", "support", "trust")
    for item in items:
        table.add_row(
            item["id"],
            item["name"],
            item["status"],
            str(item["occurrence_count"]),
            item["trust_level"],
        )
    console.print(table)


@workflows_app.command("accept")
def workflows_accept(workflow_id: Annotated[str, typer.Argument()]) -> None:
    """Accept a learned workflow so it becomes invocable."""
    from vyomel.cli.client import request

    item = request(console, "POST", f"/v1/workflows/{workflow_id}/accept")
    console.print(f"accepted {item['id']} ({item['name']})")


@workflows_app.command("reject")
def workflows_reject(workflow_id: Annotated[str, typer.Argument()]) -> None:
    """Reject a proposal and suppress re-proposal of its pattern."""
    from vyomel.cli.client import request

    item = request(console, "POST", f"/v1/workflows/{workflow_id}/reject")
    console.print(f"rejected {item['id']} ({item['name']})")


@workflows_app.command("invoke")
def workflows_invoke(
    workflow_id: Annotated[str, typer.Argument()],
    params_json: Annotated[
        str, typer.Option("--json", help="Parameter map as JSON object.")
    ] = "{}",
) -> None:
    """Expand an accepted workflow with parameters."""
    from vyomel.cli.client import request

    try:
        parameters = json.loads(params_json)
    except json.JSONDecodeError as exc:
        console.print(f"[red]invalid --json: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    body = request(
        console,
        "POST",
        f"/v1/workflows/{workflow_id}/invoke",
        json={"parameters": parameters},
    )
    console.print_json(data=body)


@voice_app.command("transcribe")
def voice_transcribe(
    text: Annotated[str, typer.Argument(help="Fixture utterance text (or path with --file)")],
    file_mode: Annotated[bool, typer.Option("--file", help="Treat argument as audio path")] = False,
) -> None:
    """Transcribe audio (fixture text shortcut by default)."""
    from vyomel.cli.client import request

    if file_mode:
        raw = Path(text).read_bytes()
        import base64

        body = request(
            console,
            "POST",
            "/v1/voice/transcribe",
            json={"audio_b64": base64.b64encode(raw).decode("ascii")},
        )
    else:
        body = request(console, "POST", "/v1/voice/transcribe", json={"text": text})
    console.print_json(data=body)


@voice_app.command("speak")
def voice_speak(
    text: Annotated[str, typer.Argument()],
) -> None:
    """Synthesize speech to the scratch voice directory."""
    from vyomel.cli.client import request

    body = request(console, "POST", "/v1/voice/speak", json={"text": text})
    console.print_json(data=body)


if __name__ == "__main__":
    app()
