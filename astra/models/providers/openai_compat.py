"""OpenAI-compatible chat provider (FR-701)."""

from __future__ import annotations

import json
from decimal import Decimal
from time import perf_counter

from astra.core.errors import ConfigError
from astra.models.types import ModelRequest, ModelResponse, ProviderInfo


class OpenAICompatibleProvider:
    """Thin adapter for OpenAI-compatible HTTP APIs (Ollama, vLLM, OpenAI)."""

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        is_remote: bool = True,
    ) -> None:
        if not base_url:
            raise ConfigError(f"{name} base_url is not configured")
        self._name = name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._is_remote = is_remote
        self._info = ProviderInfo(
            name=name,
            is_remote=is_remote,
            supports_structured_output=True,
            max_context=128_000,
            cost_per_1k_prompt=Decimal("0.01"),
            cost_per_1k_completion=Decimal("0.03"),
        )

    @property
    def info(self) -> ProviderInfo:
        return self._info

    async def complete(self, req: ModelRequest) -> ModelResponse:
        try:
            import httpx
        except ImportError as exc:
            raise ConfigError("httpx is required for cloud providers") from exc

        payload: dict[str, object] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in req.messages],
            "temperature": req.temperature,
        }
        if req.seed is not None:
            payload["seed"] = req.seed
        if req.json_schema is not None:
            payload["response_format"] = {"type": "json_object"}

        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        started = perf_counter()
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        latency_ms = (perf_counter() - started) * 1_000
        choice = body["choices"][0]["message"]["content"]
        usage = body.get("usage", {})
        parsed = None
        try:
            parsed = json.loads(choice)
        except json.JSONDecodeError:
            parsed = None
        return ModelResponse(
            content=choice,
            model=self._model,
            provider=self._name,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=latency_ms,
            parsed=parsed,
        )
