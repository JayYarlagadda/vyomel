"""Mock web tools for evals and chaos testing (no network, docs/05 §3.2)."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field

from astra.core.ids import digest_bytes
from astra.core.types import Capability
from astra.tools.base import Tool, ToolContext


class WebFetchMockInput(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class WebFetchMockOutput(BaseModel):
    url: str
    status_code: int
    title: str
    body: str


class WebFetchMock(Tool):
    """Deterministic HTTP fetch stand-in. Same URL always returns the same body."""

    name: ClassVar[str] = "web.fetch_mock"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Fetch a URL from a deterministic in-process catalog. No network egress. "
        "Used by long-run durability evals."
    )
    Input: ClassVar[type[BaseModel]] = WebFetchMockInput
    Output: ClassVar[type[BaseModel]] = WebFetchMockOutput
    base_capability: ClassVar[Capability] = Capability.L0
    idempotent: ClassVar[bool] = True
    concurrency_key: ClassVar[str | None] = "http"
    default_timeout_s: ClassVar[int] = 30

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, WebFetchMockInput)
        token = digest_bytes(params.url.encode("utf-8"))[:16]
        return WebFetchMockOutput(
            url=params.url,
            status_code=200,
            title=f"Mock document {token}",
            body=f"Deterministic body for {params.url} [{token}]",
        )
