"""Configuration and hard-ceiling enforcement.

The ceilings exist so a misconfiguration cannot turn a bug into an unbounded,
expensive, or destructive loop (docs/07-EXECUTION-ENGINE.md section 7).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from astra.core.config import CEILING_MAX_REPLANS, Settings
from astra.core.errors import ConfigError


def test_defaults_are_within_ceilings() -> None:
    settings = Settings()
    assert settings.max_replans <= CEILING_MAX_REPLANS


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_retries", 99),
        ("max_replans", 50),
        ("max_steps", 10_000),
        ("max_parallel_actions", 256),
        ("action_timeout_s", 100_000),
        ("max_wall_clock_s", 10_000_000),
        ("max_token_budget", 999_999_999),
        ("max_cost_usd", 1_000.0),
        ("approval_ttl_s", 9_999_999),
    ],
)
def test_ceilings_reject_excessive_values(field: str, value: float) -> None:
    with pytest.raises((ConfigError, ValueError)):
        Settings(**{field: value})


def test_allowed_roots_parse_windows_paths() -> None:
    # Semicolon separation matters: a colon would split drive letters.
    settings = Settings(allowed_roots="D:/Astra;D:/Astra/.astra/scratch")
    assert settings.allowed_roots == [Path("D:/Astra"), Path("D:/Astra/.astra/scratch")]


def test_sync_database_url_strips_async_driver() -> None:
    settings = Settings(database_url="postgresql+asyncpg://u:p@h:5432/db")
    assert settings.sync_database_url == "postgresql://u:p@h:5432/db"


def test_derived_directories_hang_off_workspace_root() -> None:
    settings = Settings(workspace_root=Path("D:/tmp/astra"))
    assert settings.scratch_dir == Path("D:/tmp/astra/scratch")
    assert settings.trash_dir == Path("D:/tmp/astra/trash")
    assert settings.blob_dir == Path("D:/tmp/astra/blobs")
