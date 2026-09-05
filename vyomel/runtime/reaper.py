"""Lease reaper.

A worker that dies mid-action holds a lease until ``lease_until``. This loop
returns that action to READY (or fails it if retries are exhausted) so the
DAG can continue. It is the mechanism behind FR-210 and half of FR-202.
"""

from __future__ import annotations

from datetime import datetime

from vyomel.core.clock import Clock, SystemClock
from vyomel.core.logging import get_logger
from vyomel.core.types import ActionStatus
from vyomel.runtime.state import ActionTrigger, apply_action
from vyomel.store.db import session_scope
from vyomel.store.repos import ActionRepo

log = get_logger(__name__)


class Reaper:
    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()

    async def reap(self, *, now: datetime | None = None) -> list[str]:
        moment = now or self._clock.now()
        reclaimed: list[str] = []
        async with session_scope() as session:
            repo = ActionRepo(session)
            for action in await repo.expired_leases(moment):
                if (
                    action.status is ActionStatus.RUNNING
                    and action.attempt_count >= action.max_retries
                ):
                    dest = apply_action(action.status, ActionTrigger.TOOL_FAILED_TERMINAL)
                    await repo.cas_status(
                        action.id,
                        expected=action.status,
                        new=dest,
                        finished_at=moment,
                        lease_owner=None,
                        lease_until=None,
                        error={
                            "code": "TIMEOUT",
                            "message": "lease expired and retries exhausted",
                            "retryable": False,
                        },
                    )
                    await repo.add_dead_letter(
                        action_id=action.id,
                        reason="lease_exhausted",
                        context={"attempt_count": action.attempt_count},
                    )
                    log.info("vyomel.runtime.dead_lettered", action_id=action.id)
                else:
                    dest = apply_action(action.status, ActionTrigger.LEASE_EXPIRED)
                    await repo.cas_status(
                        action.id,
                        expected=action.status,
                        new=dest,
                        lease_owner=None,
                        lease_until=None,
                    )
                    reclaimed.append(action.id)
                    from vyomel.obs.metrics import LEASES_RECLAIMED

                    LEASES_RECLAIMED.inc()
                    log.info("vyomel.runtime.reaped", action_id=action.id, dest=dest.value)
        return reclaimed
