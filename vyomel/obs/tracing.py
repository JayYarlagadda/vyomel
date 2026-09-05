"""In-process tracing (docs/10 sections 1-2).

A task's ``trace_id`` is stored on the ``tasks`` row. Workers restore W3C
``traceparent`` from the Redis payload (falling back to the task row) and
create child spans. A retry or crash-resume starts a new span with a link to
the abandoned one rather than pretending the timeline was continuous.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from vyomel.core.logging import redact

_current: ContextVar[Span | None] = ContextVar("vyomel_span", default=None)


def new_trace_id() -> str:
    return secrets.token_hex(16)


def new_span_id() -> str:
    return secrets.token_hex(8)


def root_span_id(trace_id: str) -> str:
    return trace_id[:16]


def format_traceparent(trace_id: str | None, span_id: str | None = None) -> str | None:
    if not trace_id:
        return None
    sid = span_id or root_span_id(trace_id)
    return f"00-{trace_id}-{sid}-01"


def parse_traceparent(value: str | None) -> tuple[str, str] | None:
    if not value:
        return None
    parts = value.split("-")
    if len(parts) != 4 or parts[0] != "00":
        return None
    trace_id, span_id = parts[1], parts[2]
    if len(trace_id) != 32 or len(span_id) != 16:
        return None
    return trace_id, span_id


@dataclass
class SpanLink:
    trace_id: str
    span_id: str
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class Span:
    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    start: datetime
    end: datetime | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    links: list[SpanLink] = field(default_factory=list)
    status: str = "UNSET"
    error: str | None = None

    def set(self, **attrs: Any) -> None:
        cleaned = redact({k: v for k, v in attrs.items() if v is not None})
        if isinstance(cleaned, dict):
            self.attributes.update(cleaned)

    def duration_s(self) -> float | None:
        if self.end is None:
            return None
        return (self.end - self.start).total_seconds()


class SpanRecorder:
    def __init__(self) -> None:
        self._lock = Lock()
        self._spans: list[Span] = []

    def record(self, span: Span) -> None:
        with self._lock:
            self._spans.append(span)

    def spans(self, *, trace_id: str | None = None) -> list[Span]:
        with self._lock:
            items = list(self._spans)
        if trace_id is None:
            return items
        return [s for s in items if s.trace_id == trace_id]

    def clear(self) -> None:
        with self._lock:
            self._spans.clear()


_recorder = SpanRecorder()


def recorder() -> SpanRecorder:
    return _recorder


def reset_spans() -> None:
    _recorder.clear()
    _current.set(None)


def current_span() -> Span | None:
    return _current.get()


def current_trace_id() -> str | None:
    span = _current.get()
    return None if span is None else span.trace_id


@contextmanager
def start_span(
    name: str,
    *,
    trace_id: str | None = None,
    parent_span_id: str | None = None,
    links: list[SpanLink] | None = None,
    **attrs: Any,
) -> Iterator[Span]:
    parent = _current.get()
    tid = trace_id or (parent.trace_id if parent else new_trace_id())
    pid = parent_span_id
    if pid is None and parent is not None:
        pid = parent.span_id
    span = Span(
        name=name,
        trace_id=tid,
        span_id=new_span_id(),
        parent_span_id=pid,
        start=datetime.now(UTC),
        links=list(links or ()),
    )
    span.set(**attrs)
    token = _current.set(span)
    try:
        yield span
        if span.status == "UNSET":
            span.status = "OK"
    except Exception as exc:
        span.status = "ERROR"
        span.error = type(exc).__name__
        raise
    finally:
        span.end = datetime.now(UTC)
        _recorder.record(span)
        _export_otel(span)
        _current.reset(token)


def _export_otel(span: Span) -> None:
    """Best-effort OTLP export when ``vyomel[otel]`` is installed and enabled."""
    try:
        from vyomel.core.config import get_settings

        settings = get_settings()
        if not settings.otel_enabled:
            return
    except Exception:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.trace import SpanKind, Status, StatusCode
    except ImportError:
        return
    tracer = trace.get_tracer("vyomel")
    # Export as a completed child is lossy for live parenting; the in-process
    # recorder is source of truth. This keeps Jaeger populated when the SDK
    # is configured by the process entrypoint.
    otel_span = tracer.start_span(span.name, kind=SpanKind.INTERNAL)
    for key, value in span.attributes.items():
        if isinstance(value, str | int | float | bool) or (
            isinstance(value, list) and all(isinstance(v, str) for v in value)
        ):
            otel_span.set_attribute(key, value)
    if span.status == "ERROR":
        otel_span.set_status(Status(StatusCode.ERROR, span.error or "error"))
    otel_span.end()
