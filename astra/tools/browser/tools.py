"""Browser automation tools (docs/05 §3.3)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from astra.core.config import Settings, get_settings
from astra.core.errors import ErrorCode, ToolError
from astra.core.types import Capability
from astra.tools.base import Tool, ToolContext
from astra.tools.browser.fixture import a11y_to_dict
from astra.tools.browser.session import backend_name, get_session
from astra.tools.browser.types import Target


def _settings(ctx: ToolContext) -> Settings:
    return ctx.settings or get_settings()


def _target(
    *,
    role: str | None = None,
    name: str | None = None,
    selector: str | None = None,
    ref: str | None = None,
    x: int | None = None,
    y: int | None = None,
) -> Target:
    return Target(role=role, name=name, selector=selector, ref=ref, x=x, y=y)


class OpenInput(BaseModel):
    url: str = Field(min_length=1)


class OpenOutput(BaseModel):
    url: str
    title: str
    backend: str


class ReadPageOutput(BaseModel):
    url: str
    title: str
    a11y_tree: dict[str, Any]
    dom_excerpt: str


class QueryInput(BaseModel):
    role: str | None = None
    name: str | None = None
    selector: str | None = None


class QueryOutput(BaseModel):
    ref: str
    role: str
    name: str
    actuation_tier: int


class ClickInput(BaseModel):
    role: str | None = None
    name: str | None = None
    selector: str | None = None
    ref: str | None = None
    x: int | None = None
    y: int | None = None


class ClickOutput(BaseModel):
    clicked: bool
    ref: str
    actuation_tier: int


class TypeInput(BaseModel):
    text: str = Field(max_length=8_000)
    role: str | None = None
    name: str | None = None
    selector: str | None = None
    ref: str | None = None
    allow_password: bool = False


class TypeOutput(BaseModel):
    typed: str
    ref: str
    actuation_tier: int


class SelectInput(BaseModel):
    value: str
    role: str | None = None
    name: str | None = None
    selector: str | None = None
    ref: str | None = None


class SelectOutput(BaseModel):
    selected: str
    ref: str
    actuation_tier: int


class ScrollInput(BaseModel):
    direction: Literal["up", "down"] = "down"
    amount: int = Field(default=400, ge=1, le=5_000)


class ScrollOutput(BaseModel):
    scroll: int


class SubmitInput(BaseModel):
    role: str | None = None
    name: str | None = None
    selector: str | None = None
    ref: str | None = None


class SubmitOutput(BaseModel):
    submitted: bool


class ScreenshotInput(BaseModel):
    filename: str = Field(default="page.png", min_length=1)


class ScreenshotOutput(BaseModel):
    path: str
    bytes: int


class DownloadInput(BaseModel):
    role: str | None = None
    name: str | None = None
    selector: str | None = None
    ref: str | None = None
    filename: str = Field(default="download.txt", min_length=1)


class DownloadOutput(BaseModel):
    path: str
    bytes: int
    actuation_tier: int


class BrowserOpen(Tool):
    name: ClassVar[str] = "browser.open"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Open a URL in the dedicated browser profile."
    Input: ClassVar[type[BaseModel]] = OpenInput
    Output: ClassVar[type[BaseModel]] = OpenOutput
    base_capability: ClassVar[Capability] = Capability.L1
    actuation_tier: ClassVar[int] = 3
    concurrency_key: ClassVar[str | None] = "browser"
    default_timeout_s: ClassVar[int] = 60

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, OpenInput)
        settings = _settings(ctx)
        session = await get_session(settings, task_id=ctx.task_id)
        snap = await session.open(params.url)
        return OpenOutput(url=snap.url, title=snap.title, backend=backend_name(settings))


class BrowserReadPage(Tool):
    name: ClassVar[str] = "browser.read_page"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Read the current page accessibility tree and DOM excerpt."
    Input: ClassVar[type[BaseModel]] = BaseModel
    Output: ClassVar[type[BaseModel]] = ReadPageOutput
    base_capability: ClassVar[Capability] = Capability.L0
    actuation_tier: ClassVar[int] = 2
    concurrency_key: ClassVar[str | None] = "browser"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        settings = _settings(ctx)
        session = await get_session(settings, task_id=ctx.task_id)
        snap = session.snapshot()
        return ReadPageOutput(
            url=snap.url,
            title=snap.title,
            a11y_tree=a11y_to_dict(snap.a11y_tree),
            dom_excerpt=snap.dom_excerpt,
        )


class BrowserQuery(Tool):
    name: ClassVar[str] = "browser.query"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Find an element by accessibility role/name or DOM selector."
    Input: ClassVar[type[BaseModel]] = QueryInput
    Output: ClassVar[type[BaseModel]] = QueryOutput
    base_capability: ClassVar[Capability] = Capability.L0
    actuation_tier: ClassVar[int] = 3
    concurrency_key: ClassVar[str | None] = "browser"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, QueryInput)
        if not any((params.role, params.name, params.selector)):
            raise ToolError(
                "query requires role+name or selector",
                code=ErrorCode.INVALID_PARAMETERS,
                retryable=False,
            )
        settings = _settings(ctx)
        session = await get_session(settings, task_id=ctx.task_id)
        element = await session.query(_target(**params.model_dump()))
        return QueryOutput(
            ref=element.ref,
            role=element.role,
            name=element.name,
            actuation_tier=element.actuation_tier,
        )


class BrowserClick(Tool):
    name: ClassVar[str] = "browser.click"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Click an element using role/name, selector, or coordinates."
    Input: ClassVar[type[BaseModel]] = ClickInput
    Output: ClassVar[type[BaseModel]] = ClickOutput
    base_capability: ClassVar[Capability] = Capability.L2
    actuation_tier: ClassVar[int] = 2
    concurrency_key: ClassVar[str | None] = "browser"

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        return [{"type": "value_equals", "field": "clicked", "expected": True}]

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, ClickInput)
        settings = _settings(ctx)
        session = await get_session(settings, task_id=ctx.task_id)
        payload = await session.click(_target(**params.model_dump()))
        return ClickOutput(**payload)


class BrowserType(Tool):
    name: ClassVar[str] = "browser.type"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Type text into a focused element."
    Input: ClassVar[type[BaseModel]] = TypeInput
    Output: ClassVar[type[BaseModel]] = TypeOutput
    base_capability: ClassVar[Capability] = Capability.L2
    actuation_tier: ClassVar[int] = 3
    concurrency_key: ClassVar[str | None] = "browser"

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        assert isinstance(params, TypeInput)
        return [{"type": "value_equals", "field": "typed", "expected": params.text}]

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, TypeInput)
        settings = _settings(ctx)
        session = await get_session(settings, task_id=ctx.task_id)
        payload = await session.type_text(
            _target(**params.model_dump(exclude={"text", "allow_password"})),
            params.text,
            allow_password=params.allow_password,
        )
        return TypeOutput(**payload)


class BrowserSelect(Tool):
    name: ClassVar[str] = "browser.select"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Select an option in a dropdown."
    Input: ClassVar[type[BaseModel]] = SelectInput
    Output: ClassVar[type[BaseModel]] = SelectOutput
    base_capability: ClassVar[Capability] = Capability.L1
    actuation_tier: ClassVar[int] = 3
    concurrency_key: ClassVar[str | None] = "browser"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, SelectInput)
        settings = _settings(ctx)
        session = await get_session(settings, task_id=ctx.task_id)
        payload = await session.select(_target(**params.model_dump(exclude={"value"})), params.value)
        return SelectOutput(**payload)


class BrowserScroll(Tool):
    name: ClassVar[str] = "browser.scroll"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Scroll the current page."
    Input: ClassVar[type[BaseModel]] = ScrollInput
    Output: ClassVar[type[BaseModel]] = ScrollOutput
    base_capability: ClassVar[Capability] = Capability.L1
    actuation_tier: ClassVar[int] = 3
    concurrency_key: ClassVar[str | None] = "browser"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, ScrollInput)
        settings = _settings(ctx)
        session = await get_session(settings, task_id=ctx.task_id)
        payload = await session.scroll(direction=params.direction, amount=params.amount)
        return ScrollOutput(**payload)


class BrowserSubmit(Tool):
    name: ClassVar[str] = "browser.submit"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Submit the current form."
    Input: ClassVar[type[BaseModel]] = SubmitInput
    Output: ClassVar[type[BaseModel]] = SubmitOutput
    base_capability: ClassVar[Capability] = Capability.L3
    idempotent: ClassVar[bool] = False
    actuation_tier: ClassVar[int] = 3
    concurrency_key: ClassVar[str | None] = "browser"

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        return [{"type": "value_equals", "field": "submitted", "expected": True}]

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, SubmitInput)
        settings = _settings(ctx)
        session = await get_session(settings, task_id=ctx.task_id)
        target = _target(**params.model_dump())
        payload = await session.submit(target if any(params.model_dump().values()) else None)
        return SubmitOutput(**payload)


class BrowserScreenshot(Tool):
    name: ClassVar[str] = "browser.screenshot"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Capture a screenshot of the current page into scratch."
    Input: ClassVar[type[BaseModel]] = ScreenshotInput
    Output: ClassVar[type[BaseModel]] = ScreenshotOutput
    base_capability: ClassVar[Capability] = Capability.L0
    actuation_tier: ClassVar[int] = 4
    concurrency_key: ClassVar[str | None] = "browser"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, ScreenshotInput)
        settings = _settings(ctx)
        session = await get_session(settings, task_id=ctx.task_id)
        path = ctx.scratch_dir / params.filename
        payload = await session.screenshot(path)
        return ScreenshotOutput(**payload)


class BrowserDownload(Tool):
    name: ClassVar[str] = "browser.download"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Download a linked resource into scratch."
    Input: ClassVar[type[BaseModel]] = DownloadInput
    Output: ClassVar[type[BaseModel]] = DownloadOutput
    base_capability: ClassVar[Capability] = Capability.L2
    actuation_tier: ClassVar[int] = 3
    concurrency_key: ClassVar[str | None] = "browser"
    reversible: ClassVar[bool] = False

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        assert isinstance(result, DownloadOutput)
        return [{"type": "file_exists", "path": result.path, "tier": 0}]

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, DownloadInput)
        settings = _settings(ctx)
        session = await get_session(settings, task_id=ctx.task_id)
        dest = ctx.scratch_dir / params.filename
        payload = await session.download(
            _target(**params.model_dump(exclude={"filename"})),
            dest,
        )
        return DownloadOutput(**payload)


def register_browser_tools(registry: Any) -> None:
    for tool in (
        BrowserOpen(),
        BrowserReadPage(),
        BrowserQuery(),
        BrowserClick(),
        BrowserType(),
        BrowserSelect(),
        BrowserScroll(),
        BrowserSubmit(),
        BrowserScreenshot(),
        BrowserDownload(),
    ):
        registry.register(tool)
