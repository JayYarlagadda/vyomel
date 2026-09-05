"""Text-to-speech (FR-1003)."""

from __future__ import annotations

from pathlib import Path

import pytest

from vyomel.core.errors import ErrorCode, ToolError
from vyomel.voice.tts import read_tts_text, synthesize


@pytest.mark.req("FR-1003")
def test_synthesize_writes_artifact(tmp_path: Path) -> None:
    dest = tmp_path / "out.vtts"
    artifact = synthesize("Hello from Vyomel", dest=dest)
    assert dest.exists()
    assert artifact.bytes > 0
    assert artifact.sha256
    assert artifact.duration_s > 0
    assert read_tts_text(dest) == "Hello from Vyomel"


@pytest.mark.req("FR-1003")
def test_empty_text_rejected(tmp_path: Path) -> None:
    with pytest.raises(ToolError) as exc:
        synthesize("   ", dest=tmp_path / "x.vtts")
    assert exc.value.code is ErrorCode.INVALID_PARAMETERS
