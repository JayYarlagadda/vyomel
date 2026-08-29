"""Glob matching for policy rules and escalation triggers.

``fnmatch`` is not usable here: its ``*`` crosses path separators, so
``*.pem`` would match ``notes/private.pem`` and — much worse — a rule intended
to cover one directory would silently cover the whole tree. In a policy engine
an over-broad match is a security failure, so the glob syntax is implemented
explicitly:

===========  ==============================================================
``**/``      zero or more leading path segments
``**``       anything, separators included
``*``        anything except a path separator
``?``        one character except a path separator
``[abc]``    character class, passed through to the regex
===========  ==============================================================

Paths are normalized to forward slashes and compared case-insensitively:
Astra's primary target is Windows, where ``D:\\Astra`` and ``d:/astra`` name the
same file and a case-sensitive comparison would be trivially bypassable.
"""

from __future__ import annotations

import functools
import re

_SPECIAL = ".^$+{}()|"


def normalize(value: str) -> str:
    return value.replace("\\", "/").casefold()


@functools.lru_cache(maxsize=1_024)
def _compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(_translate(normalize(pattern)), re.DOTALL)


def _translate(pattern: str) -> str:
    out: list[str] = ["^"]
    i = 0
    length = len(pattern)
    while i < length:
        char = pattern[i]
        if char == "*":
            if pattern.startswith("**/", i):
                out.append("(?:.*/)?")
                i += 3
                continue
            if pattern.startswith("**", i):
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
            i += 1
            continue
        if char == "?":
            out.append("[^/]")
            i += 1
            continue
        if char == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                out.append(r"\[")
                i += 1
                continue
            body = pattern[i + 1 : end].replace("\\", "\\\\")
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append(f"[{body}]")
            i = end + 1
            continue
        out.append(f"\\{char}" if char in _SPECIAL or char == "\\" else char)
        i += 1
    out.append("$")
    return "".join(out)


def glob_match(value: str, pattern: str) -> bool:
    """True if ``value`` matches ``pattern``.

    A bare pattern with no separator (``*.pem``) is also tested against the
    final path segment, so ``**/`` is not required for basename rules. This
    widens matches, which is the safe direction for a deny rule and is why
    ``level``/``max_level`` scoping — not glob narrowness — is what limits an
    allow rule.
    """
    subject = normalize(value)
    if _compiled(pattern).match(subject) is not None:
        return True
    if "/" not in normalize(pattern):
        tail = subject.rsplit("/", 1)[-1]
        return _compiled(pattern).match(tail) is not None
    return False


def any_match(value: str, patterns: object) -> bool:
    """Match against one pattern or a list of them. Non-strings never match."""
    if isinstance(patterns, str):
        return glob_match(value, patterns)
    if isinstance(patterns, (list, tuple)):
        return any(isinstance(p, str) and glob_match(value, p) for p in patterns)
    return False


def domain_match(host: str, pattern: str) -> bool:
    """Egress allowlist matching. ``*.example.com`` covers subdomains only."""
    host = host.casefold().rstrip(".")
    pattern = pattern.casefold().rstrip(".")
    if pattern.startswith("*."):
        return host.endswith(pattern[1:]) and host != pattern[2:]
    return host == pattern
