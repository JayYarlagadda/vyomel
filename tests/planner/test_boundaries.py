"""Prompt boundary markers."""

from __future__ import annotations

from astra.core.types import Trust
from astra.prompts.boundaries import wrap_untrusted


def test_user_content_is_not_wrapped() -> None:
    assert wrap_untrusted("hello", source="user", trust=Trust.USER) == "hello"


def test_untrusted_content_is_wrapped() -> None:
    wrapped = wrap_untrusted("payload", source="tool", trust=Trust.TOOL_UNTRUSTED)
    assert "<<<UNTRUSTED_DATA" in wrapped
    assert "payload" in wrapped
    assert "<<<END_UNTRUSTED_DATA>>>" in wrapped
