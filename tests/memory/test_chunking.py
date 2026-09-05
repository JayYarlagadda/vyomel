from __future__ import annotations

from vyomel.memory.chunking import chunk_text


def test_plain_text_is_one_chunk_when_short() -> None:
    chunks = chunk_text("hello from the notes")
    assert len(chunks) == 1
    assert chunks[0].content == "hello from the notes"
    assert chunks[0].heading_path == ()
    assert chunks[0].char_start == 0
    assert chunks[0].char_end == len("hello from the notes")


def test_markdown_splits_on_headings_and_keeps_offsets() -> None:
    text = "# Design\n\nretry policy lives here\n\n## Retry Policy\n\nexponential backoff\n"
    chunks = chunk_text(text)
    assert [c.heading_path for c in chunks] == [("Design",), ("Design", "Retry Policy")]
    retry = chunks[1]
    assert text[retry.char_start : retry.char_end] == retry.content
    assert "exponential backoff" in retry.content
