"""Shared CLI HTTP stubs.

The CLI tests assert which endpoint was called, not what FastAPI does with
the request. One recorder for every command module keeps a wrong path from
slipping through as a green test against a live server.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

import astra.cli.client as client_module


class Recorder:
    """Stands in for one HTTP round trip, remembering what was asked."""

    def __init__(self, responses: dict[tuple[str, str], Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        console: Any,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append({"method": method, "path": path, "params": params, "json": json})
        try:
            return self.responses[(method, path)]
        except KeyError:  # pragma: no cover - a test asked for an unstubbed call
            raise AssertionError(f"unexpected call: {method} {path}") from None


Install = Callable[[dict[tuple[str, str], Any]], Recorder]


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Install:
    def install(responses: dict[tuple[str, str], Any]) -> Recorder:
        rec = Recorder(responses)
        monkeypatch.setattr(client_module, "request", rec)
        return rec

    return install
