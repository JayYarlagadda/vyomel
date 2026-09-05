"""ModelProvider protocol (docs/09 §2)."""

from __future__ import annotations

from typing import Protocol

from vyomel.models.types import ModelRequest, ModelResponse, ProviderInfo


class ModelProvider(Protocol):
    @property
    def info(self) -> ProviderInfo: ...

    async def complete(self, req: ModelRequest) -> ModelResponse: ...
