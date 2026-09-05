"""Optional Playwright backend (requires ``pip install vyomel[browser]``)."""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlparse

from vyomel.core.config import Settings
from vyomel.core.errors import ErrorCode, ToolError
from vyomel.tools.browser.fixture import FixtureSession
from vyomel.tools.browser.metrics import record_actuation_tier
from vyomel.tools.browser.resolve import build_a11y_tree, dom_excerpt, parse_dom
from vyomel.tools.browser.session import fixtures_dir
from vyomel.tools.browser.types import ElementRef, PageSnapshot, Target

_lock = Lock()
_sessions: dict[str, PlaywrightSession] = {}


class PlaywrightSession:
    """Thin wrapper; falls back to parsing fixture HTML when URL uses ``fixture://``."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._fixture = FixtureSession(fixtures_dir=fixtures_dir(settings))
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    async def _ensure(self) -> Any:
        if self._page is not None:
            return self._page
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise ToolError(
                "playwright is not installed; use VYOMEL_BROWSER_BACKEND=fixture",
                code=ErrorCode.PRECONDITION_FAILED,
                retryable=False,
            ) from exc
        playwright = await async_playwright().start()
        profile = self._settings.browser_profile_dir
        profile.mkdir(parents=True, exist_ok=True)
        self._browser = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=True,
        )
        self._context = self._browser
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        return self._page

    async def open(self, url: str) -> PageSnapshot:
        if urlparse(url).scheme == "fixture":
            return self._fixture.open(url)
        page = await self._ensure()
        await page.goto(url, wait_until="domcontentloaded")
        html = await page.content()
        dom = parse_dom(html)
        title = await page.title()
        return PageSnapshot(
            url=url,
            title=title,
            a11y_tree=build_a11y_tree(dom),
            dom_excerpt=dom_excerpt(dom),
            state=self._fixture.state,
        )

    async def query(self, target: Target) -> ElementRef:
        if self._page is None or urlparse(self._fixture.url).scheme == "fixture":
            return self._fixture.query(target)
        page = self._page
        if target.role and target.name:
            locator = page.get_by_role(target.role, name=target.name)
            record_actuation_tier(2)
            await locator.first.wait_for(state="attached", timeout=5_000)
            return ElementRef(ref=f"pw:{target.role}:{target.name}", role=target.role, name=target.name, actuation_tier=2)
        if target.selector:
            locator = page.locator(target.selector)
            record_actuation_tier(3)
            await locator.first.wait_for(state="attached", timeout=5_000)
            return ElementRef(ref=f"pw:{target.selector}", role="element", name=target.selector, actuation_tier=3)
        raise ToolError("query requires role+name or selector", code=ErrorCode.INVALID_PARAMETERS)

    async def click(self, target: Target) -> dict[str, Any]:
        if self._page is None or urlparse(self._fixture.url).scheme == "fixture":
            return self._fixture.click(target)
        element = await self.query(target)
        page = self._page
        if target.role and target.name:
            await page.get_by_role(target.role, name=target.name).first.click()
        elif target.selector:
            await page.locator(target.selector).first.click()
        return {"clicked": True, "ref": element.ref, "actuation_tier": element.actuation_tier}

    async def type_text(self, target: Target, text: str, *, allow_password: bool) -> dict[str, Any]:
        if self._page is None or urlparse(self._fixture.url).scheme == "fixture":
            return self._fixture.type_text(target, text, allow_password=allow_password)
        if target.role and target.name:
            locator = self._page.get_by_role(target.role, name=target.name).first
        elif target.selector:
            locator = self._page.locator(target.selector).first
        else:
            raise ToolError("type requires role+name or selector", code=ErrorCode.INVALID_PARAMETERS)
        input_type = await locator.get_attribute("type")
        if input_type == "password" and not allow_password:
            raise ToolError(
                "refusing to type into a password field without explicit approval",
                code=ErrorCode.PERMISSION_DENIED,
                retryable=False,
            )
        await locator.fill(text)
        element = await self.query(target)
        return {"typed": text, "ref": element.ref, "actuation_tier": element.actuation_tier}

    async def select(self, target: Target, value: str) -> dict[str, Any]:
        if self._page is None or urlparse(self._fixture.url).scheme == "fixture":
            return self._fixture.select(target, value)
        if target.selector:
            await self._page.locator(target.selector).select_option(value)
        else:
            await self._page.get_by_role(target.role or "combobox", name=target.name or "").select_option(value)
        element = await self.query(target)
        return {"selected": value, "ref": element.ref, "actuation_tier": element.actuation_tier}

    async def scroll(self, *, direction: str, amount: int) -> dict[str, Any]:
        if self._page is None or urlparse(self._fixture.url).scheme == "fixture":
            return self._fixture.scroll(direction=direction, amount=amount)
        delta = amount if direction == "down" else -amount
        await self._page.mouse.wheel(0, delta)
        return {"scroll": delta}

    async def submit(self, target: Target | None = None) -> dict[str, Any]:
        if self._page is None or urlparse(self._fixture.url).scheme == "fixture":
            return self._fixture.submit(target)
        if target is not None:
            await self.click(target)
        else:
            await self._page.keyboard.press("Enter")
        return {"submitted": True}

    async def screenshot(self, path: Path) -> dict[str, Any]:
        if self._page is None or urlparse(self._fixture.url).scheme == "fixture":
            return self._fixture.screenshot(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        await self._page.screenshot(path=str(path))
        return {"path": str(path), "bytes": path.stat().st_size}

    async def download(self, target: Target, dest: Path) -> dict[str, Any]:
        if self._page is None or urlparse(self._fixture.url).scheme == "fixture":
            return self._fixture.download(target, dest)
        return self._fixture.download(target, dest)

    def snapshot(self) -> PageSnapshot:
        return self._fixture.snapshot()


async def get_playwright_session(settings: Settings, *, task_id: str) -> PlaywrightSession:
    with _lock:
        session = _sessions.get(task_id)
        if session is None:
            session = PlaywrightSession(settings)
            _sessions[task_id] = session
        return session
