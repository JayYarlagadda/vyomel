"""In-process Gmail / Calendar / GitHub / HTTP for deterministic evals."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any
from urllib.parse import urlparse

from vyomel.core.clock import Clock, SystemClock
from vyomel.core.errors import ErrorCode, ToolError
from vyomel.core.ids import new_id

_DEFAULT_ALLOW = frozenset(
    {
        "api.openai.com",
        "api.anthropic.com",
        "github.com",
        "api.github.com",
        "www.googleapis.com",
        "gmail.googleapis.com",
    }
)


def host_allowed(host: str, allow: frozenset[str] | set[str]) -> bool:
    hostname = host.lower().split(":")[0]
    for pattern in allow:
        pat = pattern.lower()
        if pat.startswith("*."):
            suffix = pat[1:]  # .example.com
            if hostname.endswith(suffix) or hostname == pat[2:]:
                return True
        elif hostname == pat:
            return True
    return False


@dataclass
class MailMessage:
    id: str
    from_addr: str
    to: list[str]
    subject: str
    body: str
    received_at: datetime
    labels: list[str] = field(default_factory=lambda: ["INBOX"])


@dataclass
class CalendarEvent:
    id: str
    title: str
    start: datetime
    end: datetime
    attendees: list[str] = field(default_factory=list)


@dataclass
class GithubIssue:
    number: int
    repo: str
    title: str
    body: str
    comments: list[str] = field(default_factory=list)


@dataclass
class FixtureWorld:
    messages: list[MailMessage] = field(default_factory=list)
    drafts: list[MailMessage] = field(default_factory=list)
    sent: list[MailMessage] = field(default_factory=list)
    events: list[CalendarEvent] = field(default_factory=list)
    issues: list[GithubIssue] = field(default_factory=list)
    http_gets: list[str] = field(default_factory=list)
    http_posts: list[str] = field(default_factory=list)


def seed_s3_world(now: datetime) -> FixtureWorld:
    yesterday = now - timedelta(days=1)
    interview_at = (now + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
    return FixtureWorld(
        messages=[
            MailMessage(
                id="msg_interview",
                from_addr="recruiter@acme.test",
                to=["me@example.com"],
                subject="Interview: Backend role — Acme",
                body=(
                    "Hi — looping back on the backend role. On-site interview is "
                    f"{interview_at.isoformat()} with Jordan Lee (jordan@acme.test). "
                    "Please block two hours of prep beforehand."
                ),
                received_at=yesterday.replace(hour=9, minute=12, second=0, microsecond=0),
            ),
            MailMessage(
                id="msg_noise",
                from_addr="alerts@pager.test",
                to=["me@example.com"],
                subject="Nightly backup succeeded",
                body="Nothing to see here.",
                received_at=yesterday.replace(hour=2, minute=0, second=0, microsecond=0),
            ),
        ],
        events=[
            CalendarEvent(
                id="evt_standup",
                title="Standup",
                start=interview_at.replace(hour=10, minute=0),
                end=interview_at.replace(hour=10, minute=30),
            ),
            CalendarEvent(
                id="evt_lunch",
                title="Lunch",
                start=interview_at.replace(hour=12, minute=0),
                end=interview_at.replace(hour=13, minute=0),
            ),
        ],
        issues=[
            GithubIssue(
                number=12,
                repo="acme/backend",
                title="Flaky lease reaper",
                body="Reproducer attached.",
            )
        ],
    )


class FixtureApi:
    def __init__(
        self,
        *,
        clock: Clock | None = None,
        allow_hosts: frozenset[str] | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        self._allow = allow_hosts or _DEFAULT_ALLOW
        self._lock = Lock()
        self._world = seed_s3_world(self._clock.now())

    def reset(self, now: datetime | None = None) -> None:
        with self._lock:
            self._world = seed_s3_world(now or self._clock.now())

    def snapshot(self) -> FixtureWorld:
        with self._lock:
            return deepcopy(self._world)

    def search_mail(self, query: str) -> list[MailMessage]:
        q = query.lower()
        with self._lock:
            return [
                m for m in self._world.messages if q in m.subject.lower() or q in m.body.lower()
            ]

    def read_mail(self, message_id: str) -> MailMessage:
        with self._lock:
            for message in self._world.messages + self._world.drafts + self._world.sent:
                if message.id == message_id:
                    return deepcopy(message)
        raise ToolError(
            f"message not found: {message_id}",
            code=ErrorCode.NOT_FOUND,
            retryable=False,
        )

    def draft_mail(self, *, to: list[str], subject: str, body: str) -> MailMessage:
        message = MailMessage(
            id=new_id(),
            from_addr="me@example.com",
            to=list(to),
            subject=subject,
            body=body,
            received_at=self._clock.now().astimezone(UTC),
            labels=["DRAFT"],
        )
        with self._lock:
            self._world.drafts.append(message)
        return deepcopy(message)

    def send_mail(
        self,
        *,
        to: list[str],
        subject: str,
        body: str,
        draft_id: str | None = None,
    ) -> MailMessage:
        if draft_id:
            with self._lock:
                for index, draft in enumerate(self._world.drafts):
                    if draft.id == draft_id:
                        sent = deepcopy(draft)
                        sent.labels = ["SENT"]
                        self._world.drafts.pop(index)
                        self._world.sent.append(sent)
                        return deepcopy(sent)
            raise ToolError("draft not found", code=ErrorCode.NOT_FOUND, retryable=False)
        message = MailMessage(
            id=new_id(),
            from_addr="me@example.com",
            to=list(to),
            subject=subject,
            body=body,
            received_at=self._clock.now().astimezone(UTC),
            labels=["SENT"],
        )
        with self._lock:
            self._world.sent.append(message)
        return deepcopy(message)

    def list_events(self, *, day: datetime) -> list[CalendarEvent]:
        start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        with self._lock:
            return [
                deepcopy(event)
                for event in self._world.events
                if event.start < end and event.end > start
            ]

    def find_free(
        self,
        *,
        day: datetime,
        duration_minutes: int,
        count: int,
        window_start_hour: int = 9,
        window_end_hour: int = 17,
    ) -> list[tuple[datetime, datetime]]:
        day_start = day.replace(hour=window_start_hour, minute=0, second=0, microsecond=0)
        day_end = day.replace(hour=window_end_hour, minute=0, second=0, microsecond=0)
        busy = sorted(self.list_events(day=day), key=lambda e: e.start)
        cursor = day_start
        slots: list[tuple[datetime, datetime]] = []
        span = timedelta(minutes=duration_minutes)
        busy_idx = 0
        while cursor + span <= day_end and len(slots) < count:
            blocked = False
            while busy_idx < len(busy) and busy[busy_idx].end <= cursor:
                busy_idx += 1
            if busy_idx < len(busy):
                event = busy[busy_idx]
                if cursor < event.end and cursor + span > event.start:
                    cursor = event.end
                    blocked = True
            if not blocked:
                slots.append((cursor, cursor + span))
                cursor = cursor + span
        return slots

    def create_event(
        self,
        *,
        title: str,
        start: datetime,
        end: datetime,
        attendees: list[str],
    ) -> CalendarEvent:
        event = CalendarEvent(
            id=new_id(),
            title=title,
            start=start,
            end=end,
            attendees=list(attendees),
        )
        with self._lock:
            self._world.events.append(event)
        return deepcopy(event)

    def delete_event(self, event_id: str) -> bool:
        with self._lock:
            before = len(self._world.events)
            self._world.events = [e for e in self._world.events if e.id != event_id]
            return len(self._world.events) < before

    def get_event(self, event_id: str) -> CalendarEvent:
        with self._lock:
            for event in self._world.events:
                if event.id == event_id:
                    return deepcopy(event)
        raise ToolError("event not found", code=ErrorCode.NOT_FOUND, retryable=False)

    def get_sent(self, message_id: str) -> MailMessage:
        with self._lock:
            for message in self._world.sent:
                if message.id == message_id:
                    return deepcopy(message)
        raise ToolError("message not found", code=ErrorCode.NOT_FOUND, retryable=False)

    def search_github(self, query: str) -> list[GithubIssue]:
        q = query.lower()
        with self._lock:
            return [
                deepcopy(issue)
                for issue in self._world.issues
                if q in issue.title.lower() or q in issue.body.lower() or q in issue.repo.lower()
            ]

    def read_github(self, repo: str, number: int) -> GithubIssue:
        with self._lock:
            for issue in self._world.issues:
                if issue.repo == repo and issue.number == number:
                    return deepcopy(issue)
        raise ToolError("issue not found", code=ErrorCode.NOT_FOUND, retryable=False)

    def create_issue(self, *, repo: str, title: str, body: str) -> GithubIssue:
        with self._lock:
            number = max((i.number for i in self._world.issues), default=0) + 1
            issue = GithubIssue(number=number, repo=repo, title=title, body=body)
            self._world.issues.append(issue)
            return deepcopy(issue)

    def comment_issue(self, *, repo: str, number: int, body: str) -> GithubIssue:
        with self._lock:
            for issue in self._world.issues:
                if issue.repo == repo and issue.number == number:
                    issue.comments.append(body)
                    return deepcopy(issue)
        raise ToolError("issue not found", code=ErrorCode.NOT_FOUND, retryable=False)

    def http_get(self, url: str) -> dict[str, Any]:
        self._assert_url(url)
        with self._lock:
            self._world.http_gets.append(url)
        return {"url": url, "status_code": 200, "body": f"fixture GET {url}"}

    def http_post(self, url: str, body: str) -> dict[str, Any]:
        self._assert_url(url)
        with self._lock:
            self._world.http_posts.append(url)
        return {"url": url, "status_code": 200, "body": f"fixture POST {len(body)} bytes"}

    def _assert_url(self, url: str) -> None:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.scheme not in {"https", "http"} or not host:
            raise ToolError("url must be http(s) with a host", code=ErrorCode.INVALID_PARAMETERS)
        if not host_allowed(host, self._allow):
            raise ToolError(
                f"egress denied for host {host}",
                code=ErrorCode.PERMISSION_DENIED,
                retryable=False,
                observation=host,
            )
