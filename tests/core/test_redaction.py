"""Secret redaction (FR-308, NFR-09).

Nothing here is optional: a single leaked credential in a log makes the whole
permission model irrelevant.
"""

from __future__ import annotations

import pytest

from astra.core.logging import REDACTED, redact, redact_processor, redact_text, register_secret


@pytest.mark.req("FR-308")
@pytest.mark.parametrize(
    "secret",
    [
        "sk-abcdefghijklmnopqrstuvwxyz012345",
        "sk-ant-abcdefghijklmnopqrstuvwxyz0123",
        "ghp_abcdefghijklmnopqrstuvwxyz012345",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r",
        "postgresql://user:hunter2@localhost:5432/db",
    ],
)
def test_patterns_are_redacted(secret: str) -> None:
    assert secret not in redact_text(f"connecting with {secret} now")


@pytest.mark.req("FR-308")
def test_secret_keys_are_redacted_regardless_of_value() -> None:
    payload = {"api_key": "plain-value", "nested": {"Authorization": "Bearer xyz"}, "safe": "ok"}
    result = redact(payload)
    assert result["api_key"] == REDACTED
    assert result["nested"]["Authorization"] == REDACTED
    assert result["safe"] == "ok"


@pytest.mark.req("FR-308")
def test_registered_values_are_redacted() -> None:
    register_secret("super-secret-token-value")
    assert "super-secret-token-value" not in redact_text("token=super-secret-token-value")


@pytest.mark.req("FR-308")
def test_redaction_traverses_collections() -> None:
    payload = {"items": [{"password": "x"}, ["sk-abcdefghijklmnopqrstuvwxyz012345"]]}
    result = redact(payload)
    assert result["items"][0]["password"] == REDACTED
    assert result["items"][1][0] == REDACTED


@pytest.mark.req("FR-308")
def test_processor_returns_plain_dict() -> None:
    event = {"event": "test", "token": "abc123456789"}
    assert redact_processor(None, "info", event)["token"] == REDACTED
