"""Model routing types (docs/09-MODEL-SERVING.md)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from astra.core.types import Sensitivity

ModelPurpose = Literal["planner.decompose", "planner.replan", "verify.judge", "chat"]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class ModelRequest:
    purpose: ModelPurpose
    messages: tuple[ChatMessage, ...]
    sensitivity: Sensitivity = Sensitivity.PUBLIC
    json_schema: dict[str, Any] | None = None
    temperature: float = 0.0
    seed: int | None = None
    max_tokens: int = 4_096


@dataclass(frozen=True, slots=True)
class ModelResponse:
    content: str
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    parsed: dict[str, Any] | None = None
    prompt_hash: str | None = None
    prompt_version: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    name: str
    is_remote: bool
    supports_structured_output: bool
    max_context: int
    cost_per_1k_prompt: Decimal = field(default_factory=lambda: Decimal("0"))
    cost_per_1k_completion: Decimal = field(default_factory=lambda: Decimal("0"))
