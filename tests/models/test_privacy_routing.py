"""Privacy routing (FR-703)."""

from __future__ import annotations

import pytest

from astra.core.config import Settings
from astra.core.errors import PrivacyRoutingViolation
from astra.core.types import Sensitivity
from astra.models.router import get_planner_provider


@pytest.mark.req("FR-703")
def test_sensitive_data_blocks_remote_openai() -> None:
    settings = Settings(env="dev", planner_backend="openai", openai_api_key="sk-test")
    with pytest.raises(PrivacyRoutingViolation):
        get_planner_provider(settings, sensitivity=Sensitivity.SENSITIVE)
