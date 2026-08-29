"""Approval decisions as a use case (FR-303, FR-304, FR-305).

The security layer owns the records and the runtime owns the state machine;
this module is where a human's answer becomes both. It is what the API and CLI
call, and it is the only place that knows how to handle the awkward case:
a user who edits parameters before approving.

**Modify is not a shortcut.** An edit is re-validated against the tool's input
schema and re-classified from scratch. If the edit raises the capability level,
the approval the user just granted no longer covers the action — the gate finds
no usable approval at the new level and asks again, this time showing what the
edited action actually does. That is why the level is stored on the approval and
compared at consumption time rather than trusted at decision time.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from astra.core.clock import Clock, SystemClock
from astra.core.config import Settings
from astra.core.errors import ConflictError, NotFoundError
from astra.core.ids import idempotency_key
from astra.core.logging import get_logger
from astra.core.types import ActionStatus, ApprovalStatus, Trust
from astra.runtime.gate import PolicyGate
from astra.security.approvals import ApprovalService, parameter_hash
from astra.security.audit import AuditEvent
from astra.security.capability import Invocation, classify
from astra.security.policy import store_for
from astra.store.models import Action, Approval
from astra.store.repos import ActionRepo, TaskRepo
from astra.tools.registry import RegistryError, ToolRegistry

log = get_logger(__name__)


class ApprovalWorkflow:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        registry: ToolRegistry,
        *,
        gate: PolicyGate | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._registry = registry
        self._clock = clock or SystemClock()
        self._gate = gate or PolicyGate(
            store_for(settings),
            approval_ttl_s=settings.approval_ttl_s,
            clock=self._clock,
        )
        self._service = ApprovalService(session, ttl_s=settings.approval_ttl_s, clock=self._clock)

    async def list(
        self,
        *,
        status: ApprovalStatus | None = ApprovalStatus.PENDING,
        task_id: str | None = None,
        limit: int = 50,
    ) -> list[Approval]:
        return await self._service.list(status=status, task_id=task_id, limit=limit)

    async def get(self, approval_id: str) -> Approval:
        return await self._service.get(approval_id)

    async def approve(
        self,
        approval_id: str,
        *,
        decided_by: str,
        modified_parameters: dict[str, Any] | None = None,
    ) -> Approval:
        approval = await self._service.get(approval_id)
        action = await self._live_action(approval)

        if modified_parameters is not None:
            await self._rewrite(action, modified_parameters)
            # Re-read: the level and parameters the gate will judge are the
            # stored ones, so the decision must be recorded against those.
            refreshed = await ActionRepo(self._session).get(action.id)
            assert refreshed is not None
            action = refreshed
            await self._service.approve(
                approval,
                decided_by=decided_by,
                modified_parameters=dict(action.parameters),
                new_parameter_hash=parameter_hash(dict(action.parameters)),
            )
        else:
            await self._service.approve(approval, decided_by=decided_by)

        await self._gate.grant(
            self._session,
            action,
            approval_id=approval.id,
            decided_by=decided_by,
            modified=modified_parameters is not None,
        )
        return approval

    async def reject(
        self, approval_id: str, *, decided_by: str, reason: str | None = None
    ) -> Approval:
        approval = await self._service.get(approval_id)
        action = await self._live_action(approval)
        await self._service.reject(approval, decided_by=decided_by)
        await self._gate.refuse(
            self._session,
            action,
            approval_id=approval.id,
            decided_by=decided_by,
            event_type=AuditEvent.APPROVAL_REJECTED,
            message=reason or "rejected by the user",
        )
        return approval

    async def _live_action(self, approval: Approval) -> Action:
        action = await ActionRepo(self._session).get(approval.action_id)
        if action is None:
            raise NotFoundError(f"action {approval.action_id} no longer exists")
        if action.status is not ActionStatus.WAITING_FOR_USER:
            # The action moved on — cancelled, or the approval already expired
            # and the sweeper failed it closed. Deciding now would resurrect it.
            raise ConflictError(
                f"action {action.id} is {action.status.value}, not awaiting approval",
                detail={"action_status": action.status.value},
            )
        return action

    async def _rewrite(self, action: Action, modified_parameters: dict[str, Any]) -> None:
        """Validate, re-classify, and store an edited invocation."""
        try:
            tool = self._registry.get(action.tool)
        except RegistryError as exc:
            raise ConflictError(f"tool {action.tool} is no longer registered") from exc
        try:
            parsed = tool.Input.model_validate(modified_parameters)
        except ValidationError as exc:
            raise ConflictError(
                f"modified parameters are not valid for {action.tool}",
                detail={"errors": exc.errors(include_url=False)},
            ) from exc

        parameters = parsed.model_dump(mode="json")
        classification = classify(
            Invocation(
                tool=tool.name,
                parameters=parameters,
                base=tool.classify(parsed),
                actuation_tier=tool.actuation_tier,
                trust=Trust.USER,
            ),
            store_for(self._settings).get().escalation,
        )

        task = await TaskRepo(self._session).get(action.task_id)
        if task is None:
            raise NotFoundError(f"task {action.task_id} no longer exists")
        if classification.level > task.capability_ceiling:
            raise ConflictError(
                f"the edit classifies as {classification.level.value}, above this task's "
                f"{task.capability_ceiling.value} ceiling",
                detail={"reasons": list(classification.reasons)},
            )

        updated = await ActionRepo(self._session).rewrite_invocation(
            action.id,
            parameters=parameters,
            capability_level=classification.level,
            idempotency_key=idempotency_key(
                tool=tool.name,
                parameters=parameters,
                task_id=action.task_id,
                step_id=action.step_id,
                plan_version=task.plan_version,
            ),
        )
        if updated is None:
            raise ConflictError(f"action {action.id} changed while being modified")
        if classification.level > action.capability_level:
            log.info(
                "astra.security.modification_escalated",
                action_id=action.id,
                was=action.capability_level.value,
                now=classification.level.value,
                reasons=list(classification.reasons),
            )
