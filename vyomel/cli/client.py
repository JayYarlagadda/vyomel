"""HTTP client for the CLI.

The CLI is a client of the API, not a second way into the domain
(docs/04-API-SPEC.md section 6). That constraint is what keeps the API complete
enough for a desktop app or a voice front-end later, so this module exists to
make going through HTTP the path of least resistance.

Errors are RFC 9457 problem documents. Rendering ``detail`` rather than a stack
trace is the difference between "PERMISSION_DENIED: rule 'protected-paths'
covers .ssh" and a traceback the user cannot act on.
"""

from __future__ import annotations

from typing import Any

import httpx
import typer
from rich.console import Console

from vyomel.core.config import Settings, get_settings


def base_url(settings: Settings | None = None) -> str:
    resolved = settings or get_settings()
    host = "127.0.0.1" if resolved.api_host == "0.0.0.0" else resolved.api_host  # noqa: S104
    return f"http://{host}:{resolved.api_port}"


def client(settings: Settings | None = None) -> httpx.Client:
    resolved = settings or get_settings()
    token = resolved.api_token.get_secret_value()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(base_url=base_url(resolved), headers=headers, timeout=30.0)


def request(
    console: Console,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
) -> Any:
    """Perform one call, or exit with a message a human can act on."""
    try:
        with client() as http:
            response = http.request(method, path, params=params, json=json)
    except httpx.ConnectError:
        console.print(
            f"[red]Cannot reach the Vyomel API at {base_url()}.[/red] Is [bold]vyomel serve[/bold] "
            "running?"
        )
        raise typer.Exit(code=2) from None
    except httpx.HTTPError as exc:
        console.print(f"[red]Request failed:[/red] {exc}")
        raise typer.Exit(code=2) from None

    if response.is_success:
        return response.json()

    console.print(f"[red]{_problem(response)}[/red]")
    raise typer.Exit(code=1)


def _problem(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}: {response.text.strip()[:400]}"
    if not isinstance(body, dict):
        return f"HTTP {response.status_code}"
    code = body.get("code") or response.status_code
    detail = body.get("detail") or body.get("title") or response.text.strip()[:400]
    if isinstance(detail, list):  # FastAPI validation errors
        detail = "; ".join(
            f"{'.'.join(str(p) for p in item.get('loc', []))}: {item.get('msg')}" for item in detail
        )
    return f"{code}: {detail}"
