"""External API tools (docs/05 §3.5)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from vyomel.core.config import Settings, get_settings
from vyomel.core.errors import ErrorCode, ToolError
from vyomel.core.oauth import require_token
from vyomel.core.types import Capability
from vyomel.tools.api.session import get_api, get_token_store_for
from vyomel.tools.base import Tool, ToolContext


def _settings(ctx: ToolContext) -> Settings:
    return ctx.settings or get_settings()


def _oauth(ctx: ToolContext, tool: str) -> None:
    settings = _settings(ctx)
    store = get_token_store_for(settings)
    require_token(store, tool, now=ctx.clock.now())


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ToolError(
            "timestamps must be timezone-aware ISO-8601",
            code=ErrorCode.INVALID_PARAMETERS,
        )
    return parsed


class EmailSearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=500)


class EmailHit(BaseModel):
    id: str
    from_addr: str
    subject: str
    received_at: str


class EmailSearchOutput(BaseModel):
    messages: list[EmailHit]


class EmailReadInput(BaseModel):
    message_id: str = Field(min_length=1)


class EmailReadOutput(BaseModel):
    id: str
    from_addr: str
    to: list[str]
    subject: str
    body: str
    received_at: str


class EmailDraftInput(BaseModel):
    to: list[str] = Field(min_length=1, max_length=20)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(max_length=20_000)


class EmailDraftOutput(BaseModel):
    draft_id: str
    subject: str


class EmailSendInput(BaseModel):
    to: list[str] = Field(default_factory=list, max_length=20)
    subject: str = Field(default="", max_length=500)
    body: str = Field(default="", max_length=20_000)
    draft_id: str | None = None


class EmailSendOutput(BaseModel):
    message_id: str
    sent: bool


class CalendarListInput(BaseModel):
    day: str = Field(min_length=1, description="ISO-8601 datetime; the date portion is used")


class CalendarEventOut(BaseModel):
    id: str
    title: str
    start: str
    end: str
    attendees: list[str]


class CalendarListOutput(BaseModel):
    events: list[CalendarEventOut]


class CalendarFindFreeInput(BaseModel):
    day: str = Field(min_length=1)
    duration_minutes: int = Field(default=60, ge=15, le=240)
    count: int = Field(default=2, ge=1, le=8)


class FreeSlot(BaseModel):
    start: str
    end: str


class CalendarFindFreeOutput(BaseModel):
    slots: list[FreeSlot]


class CalendarCreateInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start: str = Field(min_length=1)
    end: str = Field(min_length=1)
    attendees: list[str] = Field(default_factory=list, max_length=50)


class CalendarCreateOutput(BaseModel):
    id: str
    title: str
    start: str
    end: str
    attendees: list[str]


class CalendarDeleteInput(BaseModel):
    event_id: str = Field(min_length=1)


class CalendarDeleteOutput(BaseModel):
    deleted: bool
    event_id: str


class GithubSearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=200)


class GithubIssueOut(BaseModel):
    repo: str
    number: int
    title: str
    body: str
    comments: list[str]


class GithubSearchOutput(BaseModel):
    issues: list[GithubIssueOut]


class GithubReadInput(BaseModel):
    repo: str = Field(min_length=3, max_length=200)
    number: int = Field(ge=1)


class GithubIssueWriteInput(BaseModel):
    repo: str = Field(min_length=3, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=20_000)


class GithubCommentInput(BaseModel):
    repo: str = Field(min_length=3, max_length=200)
    number: int = Field(ge=1)
    body: str = Field(min_length=1, max_length=8_000)


class HttpGetInput(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class HttpGetOutput(BaseModel):
    url: str
    status_code: int
    body: str


class HttpPostInput(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    body: str = Field(default="", max_length=65_536)


class HttpPostOutput(BaseModel):
    url: str
    status_code: int
    body: str


class EmailSearch(Tool):
    name: ClassVar[str] = "email.search"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Search the Gmail inbox (read-only scope)."
    Input: ClassVar[type[BaseModel]] = EmailSearchInput
    Output: ClassVar[type[BaseModel]] = EmailSearchOutput
    base_capability: ClassVar[Capability] = Capability.L0
    actuation_tier: ClassVar[int] = 1
    concurrency_key: ClassVar[str | None] = "api"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, EmailSearchInput)
        _oauth(ctx, self.name)
        api = get_api(_settings(ctx), task_id=ctx.task_id, clock=ctx.clock)
        hits = api.search_mail(params.query)
        return EmailSearchOutput(
            messages=[
                EmailHit(
                    id=m.id,
                    from_addr=m.from_addr,
                    subject=m.subject,
                    received_at=m.received_at.isoformat(),
                )
                for m in hits
            ]
        )


class EmailRead(Tool):
    name: ClassVar[str] = "email.read"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Read a Gmail message by id (read-only scope)."
    Input: ClassVar[type[BaseModel]] = EmailReadInput
    Output: ClassVar[type[BaseModel]] = EmailReadOutput
    base_capability: ClassVar[Capability] = Capability.L0
    actuation_tier: ClassVar[int] = 1
    concurrency_key: ClassVar[str | None] = "api"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, EmailReadInput)
        _oauth(ctx, self.name)
        api = get_api(_settings(ctx), task_id=ctx.task_id, clock=ctx.clock)
        message = api.read_mail(params.message_id)
        return EmailReadOutput(
            id=message.id,
            from_addr=message.from_addr,
            to=list(message.to),
            subject=message.subject,
            body=message.body,
            received_at=message.received_at.isoformat(),
        )


class EmailDraft(Tool):
    name: ClassVar[str] = "email.draft"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Create a Gmail draft. Does not send."
    Input: ClassVar[type[BaseModel]] = EmailDraftInput
    Output: ClassVar[type[BaseModel]] = EmailDraftOutput
    base_capability: ClassVar[Capability] = Capability.L1
    actuation_tier: ClassVar[int] = 1
    concurrency_key: ClassVar[str | None] = "api"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, EmailDraftInput)
        _oauth(ctx, self.name)
        api = get_api(_settings(ctx), task_id=ctx.task_id, clock=ctx.clock)
        draft = api.draft_mail(to=params.to, subject=params.subject, body=params.body)
        return EmailDraftOutput(draft_id=draft.id, subject=draft.subject)


class EmailSend(Tool):
    name: ClassVar[str] = "email.send"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Send email. L3; never auto-approved in the shipped policy."
    Input: ClassVar[type[BaseModel]] = EmailSendInput
    Output: ClassVar[type[BaseModel]] = EmailSendOutput
    base_capability: ClassVar[Capability] = Capability.L3
    reversible: ClassVar[bool] = False
    idempotent: ClassVar[bool] = False
    actuation_tier: ClassVar[int] = 1
    concurrency_key: ClassVar[str | None] = "api"

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        return [{"type": "value_equals", "field": "sent", "expected": True}]

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, EmailSendInput)
        if not params.draft_id and (not params.to or not params.subject):
            raise ToolError(
                "email.send requires draft_id or to+subject",
                code=ErrorCode.INVALID_PARAMETERS,
                retryable=False,
            )
        _oauth(ctx, self.name)
        api = get_api(_settings(ctx), task_id=ctx.task_id, clock=ctx.clock)
        sent = api.send_mail(
            to=params.to,
            subject=params.subject,
            body=params.body,
            draft_id=params.draft_id,
        )
        return EmailSendOutput(message_id=sent.id, sent=True)


class CalendarList(Tool):
    name: ClassVar[str] = "calendar.list"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "List calendar events for a day."
    Input: ClassVar[type[BaseModel]] = CalendarListInput
    Output: ClassVar[type[BaseModel]] = CalendarListOutput
    base_capability: ClassVar[Capability] = Capability.L0
    actuation_tier: ClassVar[int] = 1
    concurrency_key: ClassVar[str | None] = "api"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, CalendarListInput)
        _oauth(ctx, self.name)
        api = get_api(_settings(ctx), task_id=ctx.task_id, clock=ctx.clock)
        events = api.list_events(day=_parse_dt(params.day))
        return CalendarListOutput(
            events=[
                CalendarEventOut(
                    id=e.id,
                    title=e.title,
                    start=e.start.isoformat(),
                    end=e.end.isoformat(),
                    attendees=list(e.attendees),
                )
                for e in events
            ]
        )


class CalendarFindFree(Tool):
    name: ClassVar[str] = "calendar.find_free"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Find free calendar slots on a day."
    Input: ClassVar[type[BaseModel]] = CalendarFindFreeInput
    Output: ClassVar[type[BaseModel]] = CalendarFindFreeOutput
    base_capability: ClassVar[Capability] = Capability.L0
    actuation_tier: ClassVar[int] = 1
    concurrency_key: ClassVar[str | None] = "api"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, CalendarFindFreeInput)
        _oauth(ctx, self.name)
        api = get_api(_settings(ctx), task_id=ctx.task_id, clock=ctx.clock)
        slots = api.find_free(
            day=_parse_dt(params.day),
            duration_minutes=params.duration_minutes,
            count=params.count,
        )
        return CalendarFindFreeOutput(
            slots=[FreeSlot(start=a.isoformat(), end=b.isoformat()) for a, b in slots]
        )


class CalendarCreateEvent(Tool):
    name: ClassVar[str] = "calendar.create_event"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Create a calendar event. L3 when it has attendees."
    Input: ClassVar[type[BaseModel]] = CalendarCreateInput
    Output: ClassVar[type[BaseModel]] = CalendarCreateOutput
    base_capability: ClassVar[Capability] = Capability.L2
    reversible: ClassVar[bool] = False
    actuation_tier: ClassVar[int] = 1
    concurrency_key: ClassVar[str | None] = "api"

    def classify(self, params: BaseModel) -> Capability:
        assert isinstance(params, CalendarCreateInput)
        if params.attendees:
            return Capability.L3
        return Capability.L2

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        assert isinstance(params, CalendarCreateInput)
        return [{"type": "value_equals", "field": "title", "expected": params.title}]

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, CalendarCreateInput)
        _oauth(ctx, self.name)
        start = _parse_dt(params.start)
        end = _parse_dt(params.end)
        if end <= start:
            raise ToolError("event end must be after start", code=ErrorCode.INVALID_PARAMETERS)
        api = get_api(_settings(ctx), task_id=ctx.task_id, clock=ctx.clock)
        event = api.create_event(
            title=params.title,
            start=start,
            end=end,
            attendees=params.attendees,
        )
        return CalendarCreateOutput(
            id=event.id,
            title=event.title,
            start=event.start.isoformat(),
            end=event.end.isoformat(),
            attendees=list(event.attendees),
        )


class CalendarDeleteEvent(Tool):
    name: ClassVar[str] = "calendar.delete_event"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Delete a calendar event."
    Input: ClassVar[type[BaseModel]] = CalendarDeleteInput
    Output: ClassVar[type[BaseModel]] = CalendarDeleteOutput
    base_capability: ClassVar[Capability] = Capability.L2
    actuation_tier: ClassVar[int] = 1
    concurrency_key: ClassVar[str | None] = "api"

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        return [{"type": "value_equals", "field": "deleted", "expected": True}]

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, CalendarDeleteInput)
        _oauth(ctx, self.name)
        api = get_api(_settings(ctx), task_id=ctx.task_id, clock=ctx.clock)
        deleted = api.delete_event(params.event_id)
        if not deleted:
            raise ToolError("event not found", code=ErrorCode.NOT_FOUND, retryable=False)
        return CalendarDeleteOutput(deleted=True, event_id=params.event_id)


class GithubSearch(Tool):
    name: ClassVar[str] = "github.search"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Search GitHub issues in the fixture/live catalog."
    Input: ClassVar[type[BaseModel]] = GithubSearchInput
    Output: ClassVar[type[BaseModel]] = GithubSearchOutput
    base_capability: ClassVar[Capability] = Capability.L0
    actuation_tier: ClassVar[int] = 1
    concurrency_key: ClassVar[str | None] = "api"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, GithubSearchInput)
        _oauth(ctx, self.name)
        api = get_api(_settings(ctx), task_id=ctx.task_id, clock=ctx.clock)
        issues = api.search_github(params.query)
        return GithubSearchOutput(issues=[_issue_out(i) for i in issues])


class GithubRead(Tool):
    name: ClassVar[str] = "github.read"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Read a GitHub issue."
    Input: ClassVar[type[BaseModel]] = GithubReadInput
    Output: ClassVar[type[BaseModel]] = GithubIssueOut
    base_capability: ClassVar[Capability] = Capability.L0
    actuation_tier: ClassVar[int] = 1
    concurrency_key: ClassVar[str | None] = "api"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, GithubReadInput)
        _oauth(ctx, self.name)
        api = get_api(_settings(ctx), task_id=ctx.task_id, clock=ctx.clock)
        return _issue_out(api.read_github(params.repo, params.number))


class GithubCreateIssue(Tool):
    name: ClassVar[str] = "github.create_issue"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Create a GitHub issue. Externally visible."
    Input: ClassVar[type[BaseModel]] = GithubIssueWriteInput
    Output: ClassVar[type[BaseModel]] = GithubIssueOut
    base_capability: ClassVar[Capability] = Capability.L3
    reversible: ClassVar[bool] = False
    idempotent: ClassVar[bool] = False
    actuation_tier: ClassVar[int] = 1
    concurrency_key: ClassVar[str | None] = "api"

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        assert isinstance(params, GithubIssueWriteInput)
        return [{"type": "value_equals", "field": "title", "expected": params.title}]

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, GithubIssueWriteInput)
        _oauth(ctx, self.name)
        api = get_api(_settings(ctx), task_id=ctx.task_id, clock=ctx.clock)
        return _issue_out(api.create_issue(repo=params.repo, title=params.title, body=params.body))


class GithubComment(Tool):
    name: ClassVar[str] = "github.comment"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Comment on a GitHub issue. Externally visible."
    Input: ClassVar[type[BaseModel]] = GithubCommentInput
    Output: ClassVar[type[BaseModel]] = GithubIssueOut
    base_capability: ClassVar[Capability] = Capability.L3
    reversible: ClassVar[bool] = False
    idempotent: ClassVar[bool] = False
    actuation_tier: ClassVar[int] = 1
    concurrency_key: ClassVar[str | None] = "api"

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        assert isinstance(params, GithubCommentInput)
        return [{"type": "value_equals", "field": "number", "expected": params.number}]

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, GithubCommentInput)
        _oauth(ctx, self.name)
        api = get_api(_settings(ctx), task_id=ctx.task_id, clock=ctx.clock)
        issue = api.comment_issue(repo=params.repo, number=params.number, body=params.body)
        return _issue_out(issue)


class HttpGet(Tool):
    name: ClassVar[str] = "http.get"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "HTTP GET against the egress allowlist."
    Input: ClassVar[type[BaseModel]] = HttpGetInput
    Output: ClassVar[type[BaseModel]] = HttpGetOutput
    base_capability: ClassVar[Capability] = Capability.L0
    actuation_tier: ClassVar[int] = 1
    concurrency_key: ClassVar[str | None] = "http"

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, HttpGetInput)
        _assert_url(params.url)
        api = get_api(_settings(ctx), task_id=ctx.task_id, clock=ctx.clock)
        payload = api.http_get(params.url)
        return HttpGetOutput(**payload)


class HttpPost(Tool):
    name: ClassVar[str] = "http.post"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "HTTP POST against the egress allowlist. Externally visible."
    Input: ClassVar[type[BaseModel]] = HttpPostInput
    Output: ClassVar[type[BaseModel]] = HttpPostOutput
    base_capability: ClassVar[Capability] = Capability.L3
    reversible: ClassVar[bool] = False
    idempotent: ClassVar[bool] = False
    actuation_tier: ClassVar[int] = 1
    concurrency_key: ClassVar[str | None] = "http"

    def verification_plan(self, params: BaseModel, result: BaseModel) -> list[dict[str, Any]]:
        return [{"type": "value_equals", "field": "status_code", "expected": 200}]

    async def execute(self, params: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(params, HttpPostInput)
        _assert_url(params.url)
        api = get_api(_settings(ctx), task_id=ctx.task_id, clock=ctx.clock)
        payload = api.http_post(params.url, params.body)
        return HttpPostOutput(**payload)


def _issue_out(issue: Any) -> GithubIssueOut:
    return GithubIssueOut(
        repo=issue.repo,
        number=issue.number,
        title=issue.title,
        body=issue.body,
        comments=list(issue.comments),
    )


def _assert_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ToolError("url must be http(s) with a host", code=ErrorCode.INVALID_PARAMETERS)


def register_api_tools(registry: Any) -> None:
    for tool in (
        EmailSearch(),
        EmailRead(),
        EmailDraft(),
        EmailSend(),
        CalendarList(),
        CalendarFindFree(),
        CalendarCreateEvent(),
        CalendarDeleteEvent(),
        GithubSearch(),
        GithubRead(),
        GithubCreateIssue(),
        GithubComment(),
        HttpGet(),
        HttpPost(),
    ):
        registry.register(tool)
