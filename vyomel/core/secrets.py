"""The ``Secret`` wrapper (docs/06-SECURITY-PERMISSIONS.md section 6).

A secret held in a plain ``str`` leaks the first time someone writes an f-string
in an exception message. Wrapping it moves the failure mode from "leaks unless
every author remembers" to "leaks only if someone explicitly calls ``.get()``",
which is greppable and reviewable.

Constructing a ``Secret`` also registers its value with the redaction filter, so
even a leak through a path that bypasses this type is scrubbed at the sink.
"""

from __future__ import annotations

import hmac
from typing import Any

from vyomel.core.logging import REDACTED, register_secret


class Secret:
    """A string that refuses to render itself."""

    __slots__ = ("_name", "_value")

    def __init__(self, value: str, *, name: str = "secret") -> None:
        self._value = value
        self._name = name
        register_secret(value)

    @property
    def name(self) -> str:
        """Identifier safe to log. Audit records name secrets, never their values."""
        return self._name

    def get(self) -> str:
        """Unwrap. The only way to reach the value, and it is easy to search for."""
        return self._value

    @property
    def is_empty(self) -> bool:
        return not self._value

    def __repr__(self) -> str:
        return f"Secret({self._name}={REDACTED})"

    def __str__(self) -> str:
        return REDACTED

    def __format__(self, _spec: str) -> str:
        return REDACTED

    def __bool__(self) -> bool:
        return bool(self._value)

    def __len__(self) -> int:
        # Length of the secret, not of the redaction placeholder: callers use
        # this for "is this plausibly a real key" checks.
        return len(self._value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Secret):
            return hmac.compare_digest(self._value, other._value)
        return NotImplemented

    def __hash__(self) -> int:
        # Deliberately not derived from the value: a hash of a secret in a log
        # or metric label is still an oracle for a short or guessable secret.
        return hash((type(self).__name__, self._name))

    def __getstate__(self) -> Any:
        raise TypeError("Secret is not serializable; call .get() at the point of use")
