"""Deterministic response cache for evaluation (FR-706)."""

from __future__ import annotations

from astra.core.ids import content_hash
from astra.models.providers.protocol import ModelProvider
from astra.models.types import ModelRequest, ModelResponse, ProviderInfo


class CachedProvider:
    def __init__(self, inner: ModelProvider) -> None:
        self._inner = inner
        self._cache: dict[str, ModelResponse] = {}

    @property
    def info(self) -> ProviderInfo:
        return self._inner.info

    def _key(self, req: ModelRequest) -> str:
        return content_hash(
            {
                "purpose": req.purpose,
                "messages": [(m.role, m.content) for m in req.messages],
                "temperature": req.temperature,
                "seed": req.seed,
            }
        )

    async def complete(self, req: ModelRequest) -> ModelResponse:
        key = self._key(req)
        hit = self._cache.get(key)
        if hit is not None:
            return ModelResponse(
                content=hit.content,
                model=hit.model,
                provider=hit.provider,
                prompt_tokens=hit.prompt_tokens,
                completion_tokens=hit.completion_tokens,
                latency_ms=0.0,
                parsed=hit.parsed,
                prompt_hash=hit.prompt_hash,
                prompt_version=hit.prompt_version,
                cache_hit=True,
            )
        response = await self._inner.complete(req)
        self._cache[key] = response
        return response
