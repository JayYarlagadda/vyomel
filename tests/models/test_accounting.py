"""Model call accounting (FR-704)."""

from __future__ import annotations

import pytest

from astra.models.accounting import AccountingProvider
from astra.models.providers.mock import MockPlannerProvider
from astra.models.types import ChatMessage, ModelRequest
from astra.store.models import ModelCall


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.req("FR-704")
async def test_accounting_persists_model_call(runtime_db) -> None:
    from sqlalchemy import select

    from astra.store.db import session_scope

    async with session_scope() as session:
        provider = AccountingProvider(MockPlannerProvider(), session)
        await provider.complete(
            ModelRequest(
                purpose="planner.decompose",
                messages=(ChatMessage(role="user", content="User instruction:\nsummarize"),),
            )
        )
        await session.flush()
        rows = list((await session.scalars(select(ModelCall))).all())
    assert len(rows) >= 1
    assert rows[-1].provider == "mock-planner"
    assert rows[0].prompt_tokens > 0
