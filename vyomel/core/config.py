"""Configuration.

Layering: field defaults -> .env -> process environment. Every setting is
prefixed ``VYOMEL_``.

Bounds are configurable but capped. ``docs/07-EXECUTION-ENGINE.md`` section 7
defines hard ceilings that configuration cannot exceed; the validators below
are where that is enforced, so a typo or a hostile config cannot turn a bug
into an unbounded, expensive, or destructive loop.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from vyomel.core.errors import ConfigError

# Hard ceilings. Not configurable, by design.
CEILING_MAX_RETRIES = 5
CEILING_MAX_REPLANS = 5
CEILING_MAX_STEPS = 100
CEILING_MAX_PARALLEL_ACTIONS = 16
CEILING_ACTION_TIMEOUT_S = 1_800
CEILING_MAX_WALL_CLOCK_S = 21_600
CEILING_MAX_TOKEN_BUDGET = 2_000_000
CEILING_MAX_COST_USD = 20.0
CEILING_APPROVAL_TTL_S = 86_400
CEILING_CANCEL_GRACE_S = 60.0
CEILING_BLOB_SPILL_THRESHOLD_BYTES = 10_485_760  # 10 MiB


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VYOMEL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- core ---
    env: Literal["dev", "test", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["console", "json"] = "console"

    # --- api ---
    api_host: str = "127.0.0.1"
    api_port: int = 8080
    api_token: SecretStr = SecretStr("")

    # --- datastores ---
    database_url: str = "postgresql+asyncpg://vyomel:vyomel@localhost:55432/vyomel"
    redis_url: str = "redis://localhost:56379/0"
    db_pool_size: int = 10
    db_max_overflow: int = 5

    # --- security ---
    policy_path: Path = Path("config/policy.yaml")

    # --- filesystem sandbox ---
    workspace_root: Path = Path("D:/Vyomel/.vyomel")
    # NoDecode: the raw value is a semicolon-separated string, not JSON, so
    # pydantic-settings must hand it to the validator below untouched.
    allowed_roots: Annotated[list[Path], NoDecode] = Field(default_factory=list)

    # --- bounds ---
    max_retries: Annotated[int, Field(ge=0)] = 2
    max_replans: Annotated[int, Field(ge=0)] = 3
    max_steps: Annotated[int, Field(ge=1)] = 40
    max_parallel_actions: Annotated[int, Field(ge=1)] = 4
    action_timeout_s: Annotated[int, Field(ge=1)] = 120
    max_wall_clock_s: Annotated[int, Field(ge=1)] = 3_600
    max_token_budget: Annotated[int, Field(ge=1)] = 200_000
    max_cost_usd: Annotated[float, Field(ge=0)] = 2.0
    approval_ttl_s: Annotated[int, Field(ge=1)] = 3_600
    # How long a RUNNING execute may keep going after the task is cancelled
    # before the worker cancels the coroutine (docs/07 §8).
    cancel_grace_s: Annotated[float, Field(ge=0)] = 10.0
    # How often a worker extends ``lease_until`` while a tool is executing (docs/07 §4.4).
    # Set to 0 to disable (tests only).
    heartbeat_interval_s: Annotated[float, Field(ge=0)] = 10.0
    # Spill ``actions.result`` to the blob store when JSON exceeds this size (docs/07 §9).
    blob_spill_threshold_bytes: Annotated[int, Field(ge=0)] = 65_536

    # --- models ---
    openai_api_key: SecretStr = SecretStr("")
    anthropic_api_key: SecretStr = SecretStr("")
    local_model_base_url: str = "http://localhost:11434/v1"
    vllm_base_url: str = ""
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    # auto: hashing in test, bge elsewhere. hashing forces the test stand-in.
    embedding_backend: Literal["auto", "hashing", "bge"] = "auto"
    planner_backend: Literal["auto", "mock", "mock-alt", "openai", "local", "vllm"] = "auto"
    vllm_model: str = "meta-llama/Meta-Llama-3.1-8B-Instruct"
    offline: bool = False

    # --- kubernetes / process topology (M13) ---
    # When false, API does not host the scheduler; run ``vyomel scheduler``.
    embed_scheduler: bool = True
    # Redis lock for multi-replica scheduler leader election.
    scheduler_lock_key: str = "vyomel:scheduler:leader"
    scheduler_lock_ttl_s: Annotated[int, Field(ge=5)] = 15
    # In-cluster workers refuse desktop.* when true (ADR-0009).
    disable_host_tools: bool = False
    # Shared secret for local-agent WebSocket registration.
    local_agent_token: SecretStr = SecretStr("")
    # Route concurrency_key=desktop actions to a connected local agent.
    local_agent_routing: bool = True

    # --- browser ---
    browser_backend: Literal["auto", "fixture", "playwright"] = "auto"
    browser_fixtures_dir: Path = Path("vyomel/tools/browser/fixtures")

    # --- desktop ---
    desktop_backend: Literal["auto", "fixture", "uia"] = "auto"
    desktop_fixtures_dir: Path = Path("vyomel/tools/desktop/fixtures")

    # --- media (M14) ---
    media_backend: Literal["auto", "fixture", "ffmpeg"] = "auto"
    media_fixtures_dir: Path = Path("vyomel/tools/media/fixtures")

    # --- voice (M16) ---
    voice_backend: Literal["auto", "fixture", "whisper"] = "auto"
    voice_wake_phrase: str = "hey vyomel"

    # --- workflow learning (M15 harden) ---
    # memory: process-local (tests/fixtures). postgres: durable via migration 0009.
    workflow_store_backend: Literal["memory", "postgres"] = "postgres"
    workflow_auto_mine: bool = True
    workflow_mine_min_support: Annotated[int, Field(ge=1)] = 3
    workflow_mine_min_length: Annotated[int, Field(ge=1)] = 3
    workflow_mine_max_tasks: Annotated[int, Field(ge=1)] = 100

    # --- external APIs / OAuth ---
    oauth_backend: Literal["auto", "memory", "file", "keyring"] = "auto"
    api_allow_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "api.openai.com",
            "api.anthropic.com",
            "github.com",
            "api.github.com",
            "*.googleapis.com",
        ]
    )

    # --- observability ---
    otel_enabled: bool = False
    otel_endpoint: str = "http://localhost:4317"
    metrics_enabled: bool = True

    @field_validator("allowed_roots", mode="before")
    @classmethod
    def _split_roots(cls, value: object) -> object:
        # Semicolon-separated so Windows drive-letter paths survive intact.
        if isinstance(value, str):
            return [Path(p.strip()) for p in value.split(";") if p.strip()]
        return value

    @field_validator("api_allow_hosts", mode="before")
    @classmethod
    def _split_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(";") if part.strip()]
        return value

    @model_validator(mode="after")
    def _enforce_ceilings(self) -> Settings:
        ceilings: list[tuple[str, float, float]] = [
            ("max_retries", self.max_retries, CEILING_MAX_RETRIES),
            ("max_replans", self.max_replans, CEILING_MAX_REPLANS),
            ("max_steps", self.max_steps, CEILING_MAX_STEPS),
            ("max_parallel_actions", self.max_parallel_actions, CEILING_MAX_PARALLEL_ACTIONS),
            ("action_timeout_s", self.action_timeout_s, CEILING_ACTION_TIMEOUT_S),
            ("max_wall_clock_s", self.max_wall_clock_s, CEILING_MAX_WALL_CLOCK_S),
            ("max_token_budget", self.max_token_budget, CEILING_MAX_TOKEN_BUDGET),
            ("max_cost_usd", self.max_cost_usd, CEILING_MAX_COST_USD),
            ("approval_ttl_s", self.approval_ttl_s, CEILING_APPROVAL_TTL_S),
            ("cancel_grace_s", self.cancel_grace_s, CEILING_CANCEL_GRACE_S),
            (
                "blob_spill_threshold_bytes",
                self.blob_spill_threshold_bytes,
                CEILING_BLOB_SPILL_THRESHOLD_BYTES,
            ),
        ]
        for name, value, ceiling in ceilings:
            if value > ceiling:
                raise ConfigError(
                    f"{name}={value} exceeds the hard ceiling of {ceiling}",
                    detail={"setting": name, "value": value, "ceiling": ceiling},
                )
        return self

    @property
    def scratch_dir(self) -> Path:
        return self.workspace_root / "scratch"

    @property
    def trash_dir(self) -> Path:
        # fs.delete moves here rather than unlinking, so L2 deletes stay reversible.
        return self.workspace_root / "trash"

    @property
    def blob_dir(self) -> Path:
        return self.workspace_root / "blobs"

    @property
    def browser_profile_dir(self) -> Path:
        return self.workspace_root / "browser-profile"

    @property
    def sync_database_url(self) -> str:
        """Alembic and other sync consumers need the psycopg-free driver stripped."""
        return self.database_url.replace("+asyncpg", "")

    def ensure_directories(self) -> None:
        for directory in (
            self.workspace_root,
            self.scratch_dir,
            self.trash_dir,
            self.blob_dir,
            self.browser_profile_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
