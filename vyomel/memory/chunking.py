"""Structure-aware chunking (docs/08-MEMORY-RAG.md §3).

Split on markdown headings first, then on a 512-token window with 64-token
overlap. A chunk that spans two unrelated headings retrieves badly regardless
of the embedding model. Offsets are into the original file so citations are
re-observable (FR-505).

"Token" here is a whitespace word. tiktoken arrives with the planner; the
window sizes match the spec so a later swap does not change the split points
enough to invalidate stored ``char_start``/``char_end``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TARGET_TOKENS = 512
OVERLAP_TOKENS = 64
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass(frozen=True, slots=True)
class Chunk:
    content: str
    heading_path: tuple[str, ...]
    char_start: int
    char_end: int
    token_count: int


def chunk_text(text: str) -> list[Chunk]:
    """Chunk markdown or plain text. Empty input yields no chunks."""
    if not text:
        return []
    chunks: list[Chunk] = []
    for path, start, end in _sections(text):
        body = text[start:end]
        if not body.strip():
            continue
        chunks.extend(_window(body, path, start))
    return chunks


def _sections(text: str) -> list[tuple[tuple[str, ...], int, int]]:
    """(heading_path, char_start, char_end) covering the whole file."""
    stack: list[tuple[int, str]] = []
    sections: list[tuple[tuple[str, ...], int, int]] = []
    cursor = 0
    pos = 0
    for line in text.splitlines(keepends=True):
        match = _HEADING.match(line.rstrip("\n"))
        if match:
            if pos > cursor:
                sections.append((_path_of(stack), cursor, pos))
            level = len(match.group(1))
            title = match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            cursor = pos + len(line)
        pos += len(line)
    if pos > cursor:
        sections.append((_path_of(stack), cursor, pos))
    if not sections:
        return [((), 0, len(text))]
    return sections


def _path_of(stack: list[tuple[int, str]]) -> tuple[str, ...]:
    return tuple(title for _, title in stack)


def _window(body: str, path: tuple[str, ...], base: int) -> list[Chunk]:
    words = _words(body)
    if not words:
        return []
    if len(words) <= TARGET_TOKENS:
        stripped = body.strip()
        rel = body.find(stripped)
        start = base + max(rel, 0)
        return [
            Chunk(
                content=stripped,
                heading_path=path,
                char_start=start,
                char_end=start + len(stripped),
                token_count=len(stripped.split()),
            )
        ]
    chunks: list[Chunk] = []
    i = 0
    while i < len(words):
        j = min(i + TARGET_TOKENS, len(words))
        start = base + words[i][1]
        end = base + words[j - 1][2]
        content = body[words[i][1] : words[j - 1][2]]
        chunks.append(
            Chunk(
                content=content,
                heading_path=path,
                char_start=start,
                char_end=end,
                token_count=j - i,
            )
        )
        if j >= len(words):
            break
        i = max(j - OVERLAP_TOKENS, i + 1)
    return chunks


def _words(text: str) -> list[tuple[str, int, int]]:
    found: list[tuple[str, int, int]] = []
    start: int | None = None
    for i, ch in enumerate(text):
        if ch.isspace():
            if start is not None:
                found.append((text[start:i], start, i))
                start = None
        elif start is None:
            start = i
    if start is not None:
        found.append((text[start:], start, len(text)))
    return found
