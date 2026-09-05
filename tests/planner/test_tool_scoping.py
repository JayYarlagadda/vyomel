"""Capability-filtered catalog for planning (FR-104)."""

from __future__ import annotations

import pytest
from tests.fakes import registry_with_fakes

from vyomel.core.types import Capability
from vyomel.orchestrator.tools import ToolCatalog
from vyomel.planner.catalog import filter_catalog
from vyomel.planner.decompose import decompose


@pytest.mark.req("FR-104")
def test_filter_catalog_respects_ceiling() -> None:
    registry = registry_with_fakes()
    catalog = ToolCatalog(registry).list()
    scoped = filter_catalog(catalog, ceiling=Capability.L0)
    assert scoped
    assert all(entry.base_capability <= Capability.L0 for entry in scoped)
    assert not any(entry.name == "fs.write_file" for entry in scoped)


@pytest.mark.asyncio
@pytest.mark.req("FR-104")
async def test_decompose_rejects_tools_above_ceiling(settings) -> None:
    registry = registry_with_fakes()
    catalog = ToolCatalog(registry).list()

    class EvilProvider:
        @property
        def info(self):

            from vyomel.models.types import ProviderInfo

            return ProviderInfo(
                name="evil",
                is_remote=False,
                supports_structured_output=True,
                max_context=8_000,
            )

        async def complete(self, req):
            from vyomel.models.types import ModelResponse

            return ModelResponse(
                content="{}",
                model="evil",
                provider="evil",
                prompt_tokens=1,
                completion_tokens=1,
                latency_ms=1.0,
                parsed={
                    "steps": [
                        {
                            "alias": "bad",
                            "title": "Bad",
                            "intent": "write",
                            "actions": [
                                {
                                    "alias": "w",
                                    "tool": "fs.write_file",
                                    "parameters": {"path": "x", "content": "y"},
                                }
                            ],
                        }
                    ]
                },
            )

    from vyomel.planner.decompose import PlannerError

    with pytest.raises(PlannerError, match="not in the capability-filtered catalog"):
        await decompose(
            "write a file",
            catalog=catalog,
            capability_ceiling=Capability.L0,
            settings=settings,
            registry=registry,
            provider=EvilProvider(),
        )
