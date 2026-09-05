"""Model call accounting (FR-704)."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from vyomel.core.ids import content_hash, new_id
from vyomel.models.providers.protocol import ModelProvider
from vyomel.models.types import ModelRequest, ModelResponse, ProviderInfo
from vyomel.store.models import ModelCall


class AccountingProvider:
    """Wraps a provider and persists one row per completion."""

    def __init__(
        self,
        inner: ModelProvider,
        session: AsyncSession | None = None,
        *,
        task_id: str | None = None,
        action_id: str | None = None,
    ) -> None:
        self._inner = inner
        self._session = session
        self._task_id = task_id
        self._action_id = action_id

    @property
    def info(self) -> ProviderInfo:
        return self._inner.info

    async def complete(self, req: ModelRequest) -> ModelResponse:
        response = await self._inner.complete(req)
        if self._session is not None:
            cost = (
                Decimal(response.prompt_tokens) * self._inner.info.cost_per_1k_prompt / 1000
                + Decimal(response.completion_tokens)
                * self._inner.info.cost_per_1k_completion
                / 1000
            )
            row = ModelCall(
                id=new_id(),
                task_id=self._task_id,
                action_id=self._action_id,
                provider=response.provider,
                model=response.model,
                purpose=req.purpose,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                ttft_ms=response.latency_ms,
                latency_ms=response.latency_ms,
                cost_usd=cost,
                sensitivity=req.sensitivity.value,
                cache_hit=getattr(response, "cache_hit", False),
                prompt_hash=content_hash(
                    {"messages": [m.content for m in req.messages], "purpose": req.purpose}
                ),
            )
            self._session.add(row)
            from vyomel.obs.metrics import MODEL_CALLS, MODEL_COST, MODEL_LATENCY, MODEL_TOKENS

            MODEL_CALLS.labels(
                provider=response.provider,
                model=response.model,
                purpose=req.purpose,
                cache_hit=str(getattr(response, "cache_hit", False)).lower(),
            ).inc()
            MODEL_TOKENS.labels(
                provider=response.provider, model=response.model, direction="prompt"
            ).inc(response.prompt_tokens)
            MODEL_TOKENS.labels(
                provider=response.provider, model=response.model, direction="completion"
            ).inc(response.completion_tokens)
            MODEL_LATENCY.labels(provider=response.provider, model=response.model).observe(
                response.latency_ms / 1000.0
            )
            MODEL_COST.labels(provider=response.provider, model=response.model).inc(float(cost))
        return response
