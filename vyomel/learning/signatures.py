"""Action signature normalization for workflow mining (docs/08 §6, FR-901).

``sig = (tool, param_shape, target_type)`` — concrete values are stripped so
recurring *shapes* of work match across tasks.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ActionSignature(BaseModel):
    tool: str
    param_shape: tuple[str, ...] = ()
    target_type: str = "none"

    def key(self) -> str:
        shape = ",".join(self.param_shape)
        return f"{self.tool}|{shape}|{self.target_type}"

    def __hash__(self) -> int:
        return hash(self.key())

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ActionSignature):
            return NotImplemented
        return self.key() == other.key()


class ObservedAction(BaseModel):
    tool: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    task_id: str | None = None


_PATH_KEYS = frozenset({"path", "src", "dest", "file", "directory", "dir"})
_URL_KEYS = frozenset({"url", "uri", "href"})
_EMAIL_KEYS = frozenset({"to", "from_addr", "email", "attendees", "cc", "bcc"})
_ID_KEYS = frozenset(
    {"id", "message_id", "event_id", "draft_id", "repo", "number", "ref", "workflow_id"}
)


def _value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def _param_shape(parameters: dict[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(f"{key}:{_value_type(value)}" for key, value in parameters.items()))


def _target_type(parameters: dict[str, Any]) -> str:
    keys = {k.lower() for k in parameters}
    if keys & _PATH_KEYS:
        return "path"
    if keys & _URL_KEYS:
        return "url"
    if keys & _EMAIL_KEYS:
        return "email"
    if keys & _ID_KEYS:
        return "id"
    if parameters:
        return "args"
    return "none"


def normalize_action(tool: str, parameters: dict[str, Any] | None = None) -> ActionSignature:
    params = parameters or {}
    return ActionSignature(
        tool=tool,
        param_shape=_param_shape(params),
        target_type=_target_type(params),
    )


def normalize_observed(action: ObservedAction) -> ActionSignature:
    return normalize_action(action.tool, action.parameters)
