"""Astra CLI.

The CLI is a client of the HTTP API, not a second entry point into the domain.
Keeping it that way guarantees the API stays complete enough to build any other
client on -- desktop UI, voice, or a wearable.

``doctor`` and ``db`` are the exceptions: they are local operational commands
that must work before the API can start.
"""

from __future__ import annotations

import asyncio
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
def doctor() -> None:
    """Verify the local environment against docs/13-ENVIRONMENT.md."""
    from astra.cli.doctor import run_doctor

    raise typer.Exit(code=0 if asyncio.run(run_doctor(console)) else 1)


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
