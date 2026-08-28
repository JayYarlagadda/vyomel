"""Structured logging with mandatory redaction.

Every record passes through :func:`redact_processor` before reaching a sink.
This is deliberately wired at the logging-configuration level rather than at
call sites: a redaction step that individual callers must remember to apply is
a redaction step that eventually gets forgotten (NFR-09).
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

from astra.core.config import Settings

# Patterns that must never reach a log, trace, prompt, or audit payload.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"(?i)\b(?:postgres(?:ql)?|redis|mysql)://[^:\s]+:[^@\s]+@"),
)

# Keys whose values are replaced wholesale regardless of shape.
_SECRET_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "session",
        "credential",
        "credentials",
        "private_key",
        "access_token",
        "refresh_token",
    }
)

REDACTED = "***REDACTED***"

_registered_values: set[str] = set()


def register_secret(value: str) -> None:
    """Register a known secret so it is scrubbed even without a matching pattern."""
    if value and len(value) >= 8:
        _registered_values.add(value)


def redact_text(text: str) -> str:
    for value in _registered_values:
        text = text.replace(value, REDACTED)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def redact(value: Any, _depth: int = 0) -> Any:
    if _depth > 8:
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            k: (REDACTED if str(k).lower() in _SECRET_KEYS else redact(v, _depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v, _depth + 1) for v in value]
    if isinstance(value, tuple):
        return tuple(redact(v, _depth + 1) for v in value)
    return value


def redact_processor(
    _logger: object, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    return dict(redact(dict(event_dict)))


def configure_logging(settings: Settings) -> None:
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_processor,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[settings.log_level]
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=settings.log_level, stream=sys.stderr, format="%(message)s")

    for key in (settings.openai_api_key, settings.anthropic_api_key, settings.api_token):
        register_secret(key.get_secret_value())


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    # Bound explicitly rather than via structlog.stdlib.add_logger_name, which
    # requires a stdlib logger factory. Astra renders through PrintLogger so
    # that log configuration does not depend on uvicorn's logging setup.
    return structlog.get_logger().bind(logger=name)  # type: ignore[no-any-return]


def bind_task_context(
    *, task_id: str | None = None, step_id: str | None = None, action_id: str | None = None
) -> None:
    """Bind correlation ids for the current async context (FR-803)."""
    bindings = {"task_id": task_id, "step_id": step_id, "action_id": action_id}
    structlog.contextvars.bind_contextvars(**{k: v for k, v in bindings.items() if v is not None})
