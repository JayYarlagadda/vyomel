"""Environment verification.

Re-checks every fact recorded in docs/13-ENVIRONMENT.md section 1 so that a
driver update, a stopped container, or a Python version change surfaces here
rather than as a confusing failure three layers deep.
"""

from __future__ import annotations

import platform
import shutil
import sys
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table


@dataclass(slots=True)
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True


async def run_doctor(console: Console) -> bool:
    checks: list[Check] = [_check_python(), _check_platform(), _check_git()]
    checks.extend(await _check_datastores())
    checks.append(_check_env_file())

    table = Table(title="astra doctor", show_lines=False)
    table.add_column("check")
    table.add_column("status")
    table.add_column("detail", overflow="fold")

    for check in checks:
        if check.ok:
            marker = "[green]PASS[/green]"
        elif check.required:
            marker = "[red]FAIL[/red]"
        else:
            marker = "[yellow]WARN[/yellow]"
        table.add_row(check.name, marker, check.detail)

    console.print(table)
    return all(check.ok for check in checks if check.required)


def _check_python() -> Check:
    major, minor = sys.version_info[:2]
    ok = (major, minor) == (3, 13)
    return Check(
        "python 3.13",
        ok,
        f"{platform.python_version()}"
        + ("" if ok else "  (see docs/13-ENVIRONMENT.md C-4: 3.14 lacks wheel coverage)"),
    )


def _check_platform() -> Check:
    return Check("platform", True, f"{platform.system()} {platform.release()}")


def _check_git() -> Check:
    path = shutil.which("git")
    return Check("git", path is not None, path or "not found on PATH")


def _check_env_file() -> Check:
    from pathlib import Path

    exists = Path(".env").exists()
    return Check(
        ".env",
        exists,
        "present" if exists else "missing -- copy .env.example to .env",
        required=False,
    )


async def _check_datastores() -> list[Check]:
    from sqlalchemy import text

    from astra.core.config import get_settings
    from astra.store.db import dispose_engine, get_engine, init_engine

    settings = get_settings()
    checks: list[Check] = []

    try:
        init_engine(settings)
        async with get_engine().connect() as conn:
            result = await conn.execute(text("SHOW server_version"))
            row = result.first()
            checks.append(Check("postgres", True, f"server {row[0] if row else 'unknown'}"))

            result = await conn.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
            row = result.first()
            checks.append(
                Check("pgvector", row is not None, f"v{row[0]}" if row else "extension missing")
            )
    except Exception as exc:
        checks.append(Check("postgres", False, str(exc)))
        checks.append(Check("pgvector", False, "skipped -- postgres unreachable"))
    finally:
        await dispose_engine()

    try:
        from redis.asyncio import Redis

        client = Redis.from_url(settings.redis_url)
        try:
            info = await client.info("server")
            checks.append(Check("redis", True, f"v{info.get('redis_version', 'unknown')}"))
        finally:
            await client.aclose()
    except Exception as exc:
        checks.append(Check("redis", False, str(exc)))

    return checks
