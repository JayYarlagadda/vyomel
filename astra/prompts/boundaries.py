"""Untrusted-content boundary markers (docs/06-SECURITY-PERMISSIONS.md section 5)."""

from __future__ import annotations

from astra.core.types import Trust

_BEGIN = "<<<UNTRUSTED_DATA source={source}>>>"
_END = "<<<END_UNTRUSTED_DATA>>>"


def wrap_untrusted(content: str, *, source: str, trust: Trust) -> str:
    if trust in {Trust.USER, Trust.SYSTEM}:
        return content
    return f"{_BEGIN.format(source=source)}\n{content}\n{_END}"
