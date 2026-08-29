"""CancellationToken is a flag tools poll; it is not a process kill."""

from __future__ import annotations

from astra.core.cancel import CancellationToken


def test_token_starts_live_and_stays_cancelled() -> None:
    token = CancellationToken()
    assert token.cancelled is False
    token.cancel()
    assert token.cancelled is True
    token.cancel()
    assert token.cancelled is True
