"""Desktop automation tools (docs/05 §3.4)."""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from vyomel.core.config import Settings, get_settings
from vyomel.core.errors import ErrorCode, ToolError
from vyomel.core.types import Capability
from vyomel.tools.base import Tool, ToolContext
from vyomel.tools.desktop.resolve import tree_to_dict
from vyomel.tools.desktop.session import backend_name, get_session
from vyomel.tools.desktop.types import Target


def _settings(ctx: ToolContext) -> Settings:
    return ctx.settings or get_settings()


def _target(
    *,
    role: str | None = None,
    name: str | None = None,
    automation_id: str | None = None,
    ref: str | None = None,
    x: int | None = None,
    y: int | None = None,
) -> Target:
    return Target(
        role=role,
        name=name,
        automation_id=automation_id,
        ref=ref,
        x=x,
        y=y,
    )


class AppOpenInput(BaseModel):
    target: str = Field(min_length=1, description="fixture://app_name or executable path")


class AppOpenOutput(BaseModel):
    title: str
    backend: str


class AppFocusInput(BaseModel):
    title: str = Field(min_length=1)


class AppFocusOutput(BaseModel):
    title: str
    focused: bool


class EmptyInput(BaseModel):
    """Tools that accept no parameters."""


class ListWindowsOutput(BaseModel):
    windows: list[str]


class ReadTreeInput(BaseModel):
    max_depth: int = Field(default=8, ge=1, le=32)


class ReadTreeOutput(BaseModel):
    title: str
    tree: dict[str, Any]


class FindElementInput(BaseModel):
    role: str | None = None
    name: str | None = None
    automation_id: str | None = None


class FindElementOutput(BaseModel):
    ref: str
    role: str
    name: str
    automation_id: str
    actuation_tier: int


class ClickElementInput(BaseModel):
    role: str | None = None
    name: str | None = None
    automation_id: str | None = None
    ref: str | None = None


class ClickElementOutput(BaseModel):
    clicked: bool
    ref: str
    actuation_tier: int


class SetFieldInput(BaseModel):
    value: str = Field(max_length=8_000)
    role: str | None = None
    name: str | None = None
    automation_id: str | None = None
    ref: str | None = None


class SetFieldOutput(BaseModel):
    value: str
    ref: str
    actuation_tier: int


class TypeTextInput(BaseModel):
    text: str = Field(max_length=8_000)
    role: str | None = None
    name: str | None = None
    automation_id: str | None = None
    ref: str | None = None
    allow_password: bool = False


class TypeTextOutput(BaseModel):
    typed: str
    ref: str
    actuation_tier: int


class KeyInput(BaseModel):
    keys: str = Field(min_length=1, description="Key chord, e.g. Ctrl+S or Enter")


class KeyOutput(BaseModel):
    keys: str
    status: str


class ClickXyInput(BaseModel):
    x: int
    y: int
    evidence_filename: str = Field(default="click_xy.png", min_length=1)


class ClickXyOutput(BaseModel):
    clicked: bool
    ref: str
    actuation_tier: int
    evidence_path: str


class ScrollInput(BaseModel):
    direction: Literal["up", "down"] = "down"
    amount: int = Field(default=200, ge=1, le=5_000)


class ScrollOutput(BaseModel):
    scroll: int


class AppOpen(Tool):
    name: ClassVar[str] = "app.open"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Open a desktop application or fixture app."
    Input: ClassVar[type[BaseModel]] = AppOpenInput
    Output: ClassVar[type[BaseModel]] = AppOpenOutput
    base_capability: ClassVar[Capability] = Capability.L1
    actuation_tier: ClassVar[int] = 1
    concurrency_key: ClassVar[str | None] = "desktop"
    default_timeout_s: ClassVar[int] = 60

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, AppOpenInput)
        settings = _settings(ctx)
        session = await get_session(settings, task_id=ctx.task_id)
        snap = session.open_app(params.target)
        return AppOpenOutput(title=snap.title, backend=backend_name(settings))


class AppFocus(Tool):
    name: ClassVar[str] = "app.focus"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Bring a desktop window to the foreground."
    Input: ClassVar[type[BaseModel]] = AppFocusInput
    Output: ClassVar[type[BaseModel]] = AppFocusOutput
    base_capability: ClassVar[Capability] = Capability.L1
    actuation_tier: ClassVar[int] = 1
    concurrency_key: ClassVar[str | None] = "desktop"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, AppFocusInput)
        settings = _settings(ctx)
        session = await get_session(settings, task_id=ctx.task_id)
        snap = session.focus(params.title)
        return AppFocusOutput(title=snap.title, focused=True)


class DesktopListWindows(Tool):
    name: ClassVar[str] = "desktop.list_windows"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "List open desktop windows."
    Input: ClassVar[type[BaseModel]] = EmptyInput
    Output: ClassVar[type[BaseModel]] = ListWindowsOutput
    base_capability: ClassVar[Capability] = Capability.L0
    actuation_tier: ClassVar[int] = 2
    concurrency_key: ClassVar[str | None] = "desktop"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        settings = _settings(ctx)
        session = await get_session(settings, task_id=ctx.task_id)
        return ListWindowsOutput(windows=session.list_windows())


class DesktopReadTree(Tool):
    name: ClassVar[str] = "desktop.read_tree"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Read the accessibility tree of the focused window."
    Input: ClassVar[type[BaseModel]] = ReadTreeInput
    Output: ClassVar[type[BaseModel]] = ReadTreeOutput
    base_capability: ClassVar[Capability] = Capability.L0
    actuation_tier: ClassVar[int] = 2
    concurrency_key: ClassVar[str | None] = "desktop"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, ReadTreeInput)
        settings = _settings(ctx)
        session = await get_session(settings, task_id=ctx.task_id)
        snap = session.snapshot()
        if snap.tree is None:
            raise ToolError(
                "no focused window",
                code=ErrorCode.PRECONDITION_FAILED,
            )
        tree_dict = tree_to_dict(snap.tree, values=session.field_values())
        tree = _truncate_tree(tree_dict, params.max_depth)
        return ReadTreeOutput(title=snap.title, tree=tree)


class DesktopFindElement(Tool):
    name: ClassVar[str] = "desktop.find_element"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Find a UI element by role/name or automation id."
    Input: ClassVar[type[BaseModel]] = FindElementInput
    Output: ClassVar[type[BaseModel]] = FindElementOutput
    base_capability: ClassVar[Capability] = Capability.L0
    actuation_tier: ClassVar[int] = 2
    concurrency_key: ClassVar[str | None] = "desktop"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, FindElementInput)
        if not any((params.role, params.name, params.automation_id)):
            raise ToolError(
                "find_element requires role+name or automation_id",
                code=ErrorCode.INVALID_PARAMETERS,
                retryable=False,
            )
        settings = _settings(ctx)
        session = await get_session(settings, task_id=ctx.task_id)
        element = session.find(_target(**params.model_dump()))
        return FindElementOutput(
            ref=element.ref,
            role=element.role,
            name=element.name,
            automation_id=element.automation_id,
            actuation_tier=element.actuation_tier,
        )


class DesktopClickElement(Tool):
    name: ClassVar[str] = "desktop.click_element"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Click a UI element using UIA invoke when possible."
    Input: ClassVar[type[BaseModel]] = ClickElementInput
    Output: ClassVar[type[BaseModel]] = ClickElementOutput
    base_capability: ClassVar[Capability] = Capability.L2
    actuation_tier: ClassVar[int] = 2
    concurrency_key: ClassVar[str | None] = "desktop"

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        return [{"type": "value_equals", "field": "clicked", "expected": True}]

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, ClickElementInput)
        settings = _settings(ctx)
        session = await get_session(settings, task_id=ctx.task_id)
        payload = await _run_sync(session.click_element, _target(**params.model_dump()))
        return ClickElementOutput(**payload)


class DesktopSetField(Tool):
    name: ClassVar[str] = "desktop.set_field"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Set a text field value via the UIA value pattern."
    Input: ClassVar[type[BaseModel]] = SetFieldInput
    Output: ClassVar[type[BaseModel]] = SetFieldOutput
    base_capability: ClassVar[Capability] = Capability.L2
    actuation_tier: ClassVar[int] = 2
    concurrency_key: ClassVar[str | None] = "desktop"

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        assert isinstance(params, SetFieldInput)
        return [{"type": "value_equals", "field": "value", "expected": params.value}]

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, SetFieldInput)
        settings = _settings(ctx)
        session = await get_session(settings, task_id=ctx.task_id)
        payload = await _run_sync(
            session.set_field,
            _target(**params.model_dump(exclude={"value"})),
            params.value,
        )
        return SetFieldOutput(**payload)


class DesktopTypeText(Tool):
    name: ClassVar[str] = "desktop.type_text"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Type text into a focused element."
    Input: ClassVar[type[BaseModel]] = TypeTextInput
    Output: ClassVar[type[BaseModel]] = TypeTextOutput
    base_capability: ClassVar[Capability] = Capability.L2
    actuation_tier: ClassVar[int] = 2
    concurrency_key: ClassVar[str | None] = "desktop"

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        assert isinstance(params, TypeTextInput)
        return [{"type": "value_equals", "field": "typed", "expected": params.text}]

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, TypeTextInput)
        settings = _settings(ctx)
        session = await get_session(settings, task_id=ctx.task_id)
        payload = await _run_sync(
            session.type_text,
            _target(**params.model_dump(exclude={"text", "allow_password"})),
            params.text,
            allow_password=params.allow_password,
        )
        return TypeTextOutput(**payload)


class DesktopKey(Tool):
    name: ClassVar[str] = "desktop.key"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Send a keyboard shortcut or key press."
    Input: ClassVar[type[BaseModel]] = KeyInput
    Output: ClassVar[type[BaseModel]] = KeyOutput
    base_capability: ClassVar[Capability] = Capability.L2
    actuation_tier: ClassVar[int] = 2
    concurrency_key: ClassVar[str | None] = "desktop"

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        assert isinstance(params, KeyInput)
        normalized = params.keys.lower().replace(" ", "")
        if normalized in {"enter", "return"}:
            return [{"type": "value_equals", "field": "status", "expected": "submitted"}]
        if normalized in {"ctrl+s", "control+s"}:
            return [{"type": "value_equals", "field": "status", "expected": "saved"}]
        return []

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, KeyInput)
        settings = _settings(ctx)
        session = await get_session(settings, task_id=ctx.task_id)
        payload = await _run_sync(session.key, params.keys)
        return KeyOutput(**payload)


class DesktopClickXy(Tool):
    name: ClassVar[str] = "desktop.click_xy"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Click screen coordinates as a last resort; captures evidence."
    Input: ClassVar[type[BaseModel]] = ClickXyInput
    Output: ClassVar[type[BaseModel]] = ClickXyOutput
    base_capability: ClassVar[Capability] = Capability.L2
    actuation_tier: ClassVar[int] = 4
    concurrency_key: ClassVar[str | None] = "desktop"

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        return [{"type": "value_equals", "field": "clicked", "expected": True}]

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, ClickXyInput)
        settings = _settings(ctx)
        session = await get_session(settings, task_id=ctx.task_id)
        evidence = ctx.scratch_dir / params.evidence_filename
        await _run_sync(session.capture_evidence, evidence)
        payload = await _run_sync(session.click_xy, params.x, params.y)
        return ClickXyOutput(**payload, evidence_path=str(evidence))


class DesktopScroll(Tool):
    name: ClassVar[str] = "desktop.scroll"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Scroll the focused window."
    Input: ClassVar[type[BaseModel]] = ScrollInput
    Output: ClassVar[type[BaseModel]] = ScrollOutput
    base_capability: ClassVar[Capability] = Capability.L1
    actuation_tier: ClassVar[int] = 2
    concurrency_key: ClassVar[str | None] = "desktop"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, ScrollInput)
        settings = _settings(ctx)
        session = await get_session(settings, task_id=ctx.task_id)
        payload = await _run_sync(session.scroll, direction=params.direction, amount=params.amount)
        return ScrollOutput(**payload)


def _truncate_tree(node: dict[str, Any], max_depth: int, depth: int = 0) -> dict[str, Any]:
    children = node.get("children", [])
    if depth >= max_depth:
        return {**node, "children": []}
    return {
        **node,
        "children": [_truncate_tree(child, max_depth, depth + 1) for child in children],
    }


async def _run_sync(func: Any, *args: Any, **kwargs: Any) -> Any:
    return func(*args, **kwargs)


def register_desktop_tools(registry: Any) -> None:
    for tool in (
        AppOpen(),
        AppFocus(),
        DesktopListWindows(),
        DesktopReadTree(),
        DesktopFindElement(),
        DesktopClickElement(),
        DesktopSetField(),
        DesktopTypeText(),
        DesktopKey(),
        DesktopClickXy(),
        DesktopScroll(),
    ):
        registry.register(tool)
