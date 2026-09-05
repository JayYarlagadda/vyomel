"""Load purpose → backend routing from ``config/models.yaml``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

DEFAULT_ROUTING: dict[str, Any] = {
    "planner": {"default": "mock"},
    "purposes": {
        "planner.decompose": {"prefer": ["cloud", "vllm", "local", "mock"]},
        "planner.replan": {"prefer": ["cloud", "vllm", "local", "mock"]},
        "verify.judge": {"prefer": ["local", "vllm", "mock"]},
        "chat": {"prefer": ["local", "vllm", "cloud", "mock"]},
        "extract": {"prefer": ["local", "mock"]},
        "classify": {"prefer": ["local", "mock"]},
        "summarize": {"prefer": ["local", "mock"]},
        "embed": {"prefer": ["local"]},
    },
    "providers": {
        "mock": {"backend": "mock"},
        "mock-alt": {"backend": "mock-alt"},
        "local": {"backend": "local", "is_remote": False},
        "openai": {"backend": "openai", "is_remote": True},
        "vllm": {"backend": "vllm", "is_remote": True},
    },
}


@lru_cache(maxsize=4)
def load_model_config(path: str | None = None) -> dict[str, Any]:
    resolved = Path(path) if path else Path("config/models.yaml")
    if not resolved.is_file():
        return dict(DEFAULT_ROUTING)
    raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    merged = dict(DEFAULT_ROUTING)
    merged.update(raw)
    if "purposes" in raw:
        purposes = dict(DEFAULT_ROUTING["purposes"])
        purposes.update(raw["purposes"])
        merged["purposes"] = purposes
    if "providers" in raw:
        providers = dict(DEFAULT_ROUTING["providers"])
        providers.update(raw["providers"])
        merged["providers"] = providers
    return merged


def preferred_backends(purpose: str, *, config: dict[str, Any] | None = None) -> list[str]:
    cfg = config or load_model_config()
    entry = (cfg.get("purposes") or {}).get(purpose) or {}
    prefer = entry.get("prefer")
    if isinstance(prefer, list) and prefer:
        return [str(item) for item in prefer]
    planner_default = (cfg.get("planner") or {}).get("default", "mock")
    return [str(planner_default)]
