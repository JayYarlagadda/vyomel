"""Tool catalog and debug invoke.

The CLI and any other client list and call tools only through HTTP. This
module is the orchestrator seam: it has the registry, the classifier, and the
policy store, and it is the only place a direct invoke is allowed to run a
tool. Direct invoke is a debug path, not a way around the gate — ``CONFIRM``
and ``DENY`` fail closed, and every decision is audited.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from vyomel.core.cancel import CancellationToken
from vyomel.core.clock import Clock, SystemClock
from vyomel.core.config import Settings
from vyomel.core.errors import ConfigError, PermissionDeniedError, ToolError
from vyomel.core.ids import new_id
from vyomel.core.types import Capability, Decision, Trust
from vyomel.security.audit import AuditEvent, AuditTrail
from vyomel.security.capability import Invocation, classify
from vyomel.security.policy import PolicyRequest, store_for
from vyomel.tools.base import Tool, ToolContext
from vyomel.tools.catalog import CatalogEntry
from vyomel.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class InvokeResult:
    invoke_id: str
    tool: str
    capability_level: Capability
    decision: Decision
    result: dict[str, Any]


class ToolCatalog:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def list(self) -> list[CatalogEntry]:
        return [self.show(name) for name in self._registry.names()]

    def show(self, name: str) -> CatalogEntry:
        tool = self._registry.get(name)
        return CatalogEntry(
            name=tool.name,
            version=tool.version,
            description=tool.description,
            base_capability=tool.base_capability,
            reversible=tool.reversible,
            idempotent=tool.idempotent,
            actuation_tier=tool.actuation_tier,
            concurrency_key=tool.concurrency_key,
            input_schema=tool.Input.model_json_schema(),
            output_schema=tool.Output.model_json_schema(),
        )


class DirectInvoker:
    """Run one tool in-process, still fully policy-gated and audited.

    There is no approval collection on this path. An action that would stop
    for consent in the worker is refused here, with the same policy record
    the worker would have written. Post-action verification stays on the
    worker: this path is an operator probe, not a second action lifecycle.
    """

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        registry: ToolRegistry,
        *,
        clock: Clock | None = None,
        audit: AuditTrail | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._registry = registry
        self._clock = clock or SystemClock()
        self._audit = audit or AuditTrail(self._clock)

    async def invoke(self, name: str, parameters: dict[str, Any]) -> InvokeResult:
        tool = self._registry.get(name)
        try:
            parsed = tool.Input.model_validate(parameters)
        except ValidationError as exc:
            raise ConfigError(
                f"parameters are not valid for {name}",
                detail={"errors": exc.errors(include_url=False)},
            ) from exc

        resolved = parsed.model_dump(mode="json")
        policy = store_for(self._settings).get()
        classification = classify(
            Invocation(
                tool=tool.name,
                parameters=resolved,
                base=tool.classify(parsed),
                actuation_tier=tool.actuation_tier,
                trust=Trust.USER,
            ),
            policy.escalation,
        )
        decision = policy.evaluate(
            PolicyRequest(
                tool=tool.name,
                level=classification.level,
                parameters=resolved,
            )
        )
        invoke_id = new_id()
        event = {
            Decision.ALLOW: AuditEvent.POLICY_ALLOWED,
            Decision.CONFIRM: AuditEvent.POLICY_CONFIRM,
            Decision.DENY: AuditEvent.POLICY_DENIED,
        }[decision.decision]
        await self._audit.append(
            self._session,
            actor="origin:invoke",
            event_type=event,
            action_id=invoke_id,
            capability_level=classification.level,
            payload={"tool": tool.name, "direct": True, **decision.to_payload()},
        )
        # Policy must be durable before any world mutation, and before a 403
        # rolls the request session back.
        await self._session.commit()

        if decision.decision is Decision.DENY:
            raise PermissionDeniedError(
                decision.reason,
                detail={"tool": tool.name, "rule_id": decision.rule_id},
            )
        if decision.decision is Decision.CONFIRM:
            raise PermissionDeniedError(
                "direct invoke does not collect consent; submit a task so the "
                "approval queue can present this action",
                detail={"tool": tool.name, "rule_id": decision.rule_id},
            )

        ctx = ToolContext(
            task_id=f"invoke:{invoke_id}",
            action_id=invoke_id,
            capability_granted=classification.level,
            scratch_dir=self._settings.scratch_dir,
            allowed_roots=list(self._settings.allowed_roots),
            deadline=self._clock.now() + timedelta(seconds=tool.default_timeout_s),
            cancel=CancellationToken(),
            clock=self._clock,
            trash_dir=self._settings.trash_dir,
        )
        pre = await tool.preflight(parsed, ctx)
        if not pre.ok:
            await self._finish_error(
                invoke_id, tool, classification.level, pre.reason or "preflight failed"
            )
            raise ToolError(pre.reason or "preflight failed")

        try:
            output = await tool.execute(parsed, ctx)
        except Exception as exc:
            await self._finish_error(invoke_id, tool, classification.level, str(exc))
            raise

        result = output.model_dump(mode="json")
        await self._audit.append(
            self._session,
            actor="origin:invoke",
            event_type=AuditEvent.TOOL_INVOKED,
            action_id=invoke_id,
            capability_level=classification.level,
            payload={"tool": tool.name, "status": "ok", "result_keys": sorted(result)},
        )
        return InvokeResult(
            invoke_id=invoke_id,
            tool=tool.name,
            capability_level=classification.level,
            decision=decision.decision,
            result=result,
        )

    async def _finish_error(
        self, invoke_id: str, tool: Tool, level: Capability, error: str
    ) -> None:
        await self._audit.append(
            self._session,
            actor="origin:invoke",
            event_type=AuditEvent.TOOL_INVOKED,
            action_id=invoke_id,
            capability_level=level,
            payload={"tool": tool.name, "status": "error", "error": error},
        )
        await self._session.commit()


__all__ = ["CatalogEntry", "DirectInvoker", "InvokeResult", "ToolCatalog"]
