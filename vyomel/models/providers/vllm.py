"""vLLM provider (FR-707) — OpenAI-compatible adapter for self-hosted serving."""

from __future__ import annotations

from decimal import Decimal

from vyomel.core.config import Settings
from vyomel.core.errors import ConfigError
from vyomel.models.providers.openai_compat import OpenAICompatibleProvider
from vyomel.models.types import ProviderInfo


class VllmProvider(OpenAICompatibleProvider):
    """Production adapter for a vLLM OpenAI-compatible endpoint.

    The MX330 cannot run vLLM (ADR-0006). Day-to-day this points at a rented
    GPU via ``VYOMEL_VLLM_BASE_URL`` (SSH tunnel or public URL). Tests use a
    fixture OpenAI-compatible server from ``evals/suites/serving/``.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str = "meta-llama/Meta-Llama-3.1-8B-Instruct",
        api_key: str = "EMPTY",
        max_context: int = 32_768,
    ) -> None:
        if not base_url:
            raise ConfigError(
                "vLLM base_url is not configured; set VYOMEL_VLLM_BASE_URL "
                "(see infra/vllm/ and docs/09-MODEL-SERVING.md §5)"
            )
        super().__init__(
            name="vllm",
            base_url=base_url,
            api_key=api_key,
            model=model,
            is_remote=True,
        )
        # Self-hosted: no per-token bill from a vendor, but still tracked for
        # cost-of-ownership comparisons against hosted APIs.
        self._info = ProviderInfo(
            name="vllm",
            is_remote=True,
            supports_structured_output=True,
            max_context=max_context,
            cost_per_1k_prompt=Decimal("0"),
            cost_per_1k_completion=Decimal("0"),
        )

    @classmethod
    def from_settings(cls, settings: Settings, *, model: str | None = None) -> VllmProvider:
        return cls(
            base_url=settings.vllm_base_url,
            model=model or settings.vllm_model,
        )
