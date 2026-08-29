"""Cooperative cancellation.

Each in-flight execute gets its own token. The worker sets it when the task
is cancelled; tools poll ``cancelled`` between I/O. After ``cancel_grace_s``
the worker cancels the execute task and abandons the lease — it does not CAS
``RUNNING → CANCELLED`` from the canceller, which would race a mutation that
has not been committed yet.
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
