"""Test-only tools.

Every production tool is too well-behaved to exercise a timeout, a side-effect
reservation, a policy gate, or a lying write. These exist to provide exactly
those behaviors. Registering a purpose-built tool is preferable to monkeypatching
a real one: ``tests/tools/test_contract.py`` asserts properties of the production
registry, and a test that mutates `fs.write_file` would make those assertions
describe something that never ships.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from astra.core.errors import ErrorCode, ToolError
from astra.core.ids import digest_bytes
from astra.core.types import Capability
from astra.tools.base import Tool, ToolContext
from astra.tools.registry import ToolRegistry, default_registry
from astra.tools.sandbox import resolve_in_sandbox


class SleepInput(BaseModel):
    seconds: float = Field(ge=0.0, le=60.0)


class SleepOutput(BaseModel):
    slept_s: float


class Sleep(Tool):
    """Sleeps longer than its own timeout when asked to, so FR-205 is observable."""

    name: ClassVar[str] = "test.sleep"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Sleep for a number of seconds. Test fixture only."
    Input: ClassVar[type[BaseModel]] = SleepInput
    Output: ClassVar[type[BaseModel]] = SleepOutput
    base_capability: ClassVar[Capability] = Capability.L0
    idempotent: ClassVar[bool] = True
    # Deliberately short: an integration test must not wait out a real timeout.
    default_timeout_s: ClassVar[int] = 1

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, SleepInput)
        remaining = params.seconds
        while remaining > 0:
            if ctx.cancel.cancelled:
                raise ToolError("cancelled", code=ErrorCode.PRECONDITION_FAILED, retryable=False)
            slice_s = min(0.05, remaining)
            await asyncio.sleep(slice_s)
            remaining -= slice_s
        return SleepOutput(slept_s=params.seconds)


class NotifyInput(BaseModel):
    recipient: str
    body: str = ""


class NotifyOutput(BaseModel):
    delivered_to: str


class Notify(Tool):
    """An L3 externally-visible, non-idempotent effect, for approval-gate tests."""

    name: ClassVar[str] = "test.notify"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Pretend to notify someone outside the machine. Test fixture only."
    Input: ClassVar[type[BaseModel]] = NotifyInput
    Output: ClassVar[type[BaseModel]] = NotifyOutput
    base_capability: ClassVar[Capability] = Capability.L3
    idempotent: ClassVar[bool] = False
    delivered: ClassVar[list[str]] = []

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        assert isinstance(params, NotifyInput)
        return [{"verifier": "value_equals", "field": "delivered_to", "expected": params.recipient}]

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, NotifyInput)
        Notify.delivered.append(params.recipient)
        return NotifyOutput(delivered_to=params.recipient)


class OpaqueInput(BaseModel):
    note: str = "done"


class OpaqueOutput(BaseModel):
    ok: bool = True


class Opaque(Tool):
    """L1 tool whose only postcondition has no observation path.

    Used to prove that UNVERIFIED does not promote the task to SUCCEEDED
    unless the step opted in with ``tolerates_unverified``.
    """

    name: ClassVar[str] = "test.opaque"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Succeeds without a verifiable world change. Test fixture only."
    Input: ClassVar[type[BaseModel]] = OpaqueInput
    Output: ClassVar[type[BaseModel]] = OpaqueOutput
    base_capability: ClassVar[Capability] = Capability.L1
    idempotent: ClassVar[bool] = True

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        return [{"type": "llm_judge", "expected": "looks right"}]

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        return OpaqueOutput(ok=True)


class LyingWriteInput(BaseModel):
    path: str
    claimed: str
    actual: str


class LyingWriteOutput(BaseModel):
    path: str
    sha256: str


class LyingWrite(Tool):
    """Writes ``actual`` but reports the hash of ``claimed``.

    The catch-rate test: verification must re-read the file and FAIL, even
    though the tool result looks like a successful write of ``claimed``.
    """

    name: ClassVar[str] = "test.lying_write"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Writes the wrong bytes and lies about the hash. Test fixture only."
    )
    Input: ClassVar[type[BaseModel]] = LyingWriteInput
    Output: ClassVar[type[BaseModel]] = LyingWriteOutput
    base_capability: ClassVar[Capability] = Capability.L1
    idempotent: ClassVar[bool] = True

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        assert isinstance(params, LyingWriteInput)
        return [
            {
                "type": "file_hash",
                "path": params.path,
                "expected": digest_bytes(params.claimed.encode("utf-8")),
                "tier": 1,
            }
        ]

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, LyingWriteInput)
        target = resolve_in_sandbox(params.path, ctx.allowed_roots)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(params.actual, encoding="utf-8")
        return LyingWriteOutput(
            path=str(target),
            sha256=digest_bytes(params.claimed.encode("utf-8")),
        )


class FailHardInput(BaseModel):
    reason: str = "boom"


class FailHardOutput(BaseModel):
    ok: bool = False


class FailHard(Tool):
    """Always fails without retry — for replan tests."""

    name: ClassVar[str] = "test.fail_hard"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Fails permanently. Test fixture only."
    Input: ClassVar[type[BaseModel]] = FailHardInput
    Output: ClassVar[type[BaseModel]] = FailHardOutput
    base_capability: ClassVar[Capability] = Capability.L0
    idempotent: ClassVar[bool] = True

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        raise ToolError(
            getattr(params, "reason", "boom"),
            code=ErrorCode.PRECONDITION_FAILED,
            retryable=False,
        )


class HoldInput(BaseModel):
    pass


class HoldOutput(BaseModel):
    held: bool = True


_hold_started = asyncio.Event()
_hold_release = asyncio.Event()


def reset_hold() -> None:
    _hold_started.clear()
    _hold_release.clear()


def signal_hold_started() -> asyncio.Event:
    return _hold_started


def release_hold() -> None:
    _hold_release.set()


class Hold(Tool):
    """Blocks until ``release_hold()`` — for lease-heartbeat tests."""

    name: ClassVar[str] = "test.hold"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Block until released. Test fixture only."
    Input: ClassVar[type[BaseModel]] = HoldInput
    Output: ClassVar[type[BaseModel]] = HoldOutput
    base_capability: ClassVar[Capability] = Capability.L0
    idempotent: ClassVar[bool] = True
    default_timeout_s: ClassVar[int] = 30

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        _hold_started.set()
        await _hold_release.wait()
        return HoldOutput()


def registry_with_fakes() -> ToolRegistry:
    registry = default_registry()
    registry.register(Sleep())
    registry.register(Notify())
    registry.register(Opaque())
    registry.register(LyingWrite())
    registry.register(FailHard())
    registry.register(Hold())
    return registry
