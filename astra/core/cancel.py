"""Cooperative cancellation.

Tools check ``cancelled`` at well-defined points; the worker sets it when the
task is cancelled. M1 tools are short enough that a start-of-execute check is
enough. Long-running actuators (M7+) poll between I/O.
"""

from __future__ import annotations


class CancellationToken:
    __slots__ = ("_cancelled",)

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled
