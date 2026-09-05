"""Voice suite: wake → utterance → speak → barge-in (M16)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vyomel.voice.session import VoiceSession, fixture_utterance
from vyomel.voice.wake import detect_wake


async def run(work: Path) -> dict[str, object]:
    session = VoiceSession()
    work.mkdir(parents=True, exist_ok=True)

    assert await session.listen_wake(fixture_utterance("background chatter")) is False
    assert await session.listen_wake(fixture_utterance("hey vyomel file the Orbit notes")) is True
    utterance = await session.listen_utterance(fixture_utterance("hey vyomel file the Orbit notes"))
    assert "orbit" in utterance.text.lower()
    assert detect_wake(utterance).detected is False  # wake stripped

    speech = await session.speak(
        "Filing Orbit notes now",
        dest=str(work / "reply.vtts"),
    )
    assert Path(speech.path).exists()
    await asyncio.sleep(0.05)
    cancelled = session.barge_in()
    assert cancelled is True
    await session.wait_speech_done()

    return {
        "success": True,
        "utterance": utterance.text,
        "speech_bytes": speech.bytes,
        "barge_in_count": session.barge_in_count,
        "backend": "fixture",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("evals/results/2026-09-04-m16"))
    args = parser.parse_args()
    import tempfile

    with tempfile.TemporaryDirectory(prefix="vyomel-voice-") as tmp:
        result = asyncio.run(run(Path(tmp)))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    report = f"""# M16 voice — 2026-09-04

Fixture STT/TTS session: wake-word gate, utterance capture, speak, barge-in.

## Results

| metric | value |
|---|---:|
| success | {result["success"]} |
| barge_in_count | {result["barge_in_count"]} |
| speech_bytes | {result["speech_bytes"]} |
| backend | {result["backend"]} |

## Reproduce

```powershell
python evals/suites/voice/run.py
pytest tests/voice
```
"""
    (args.out / "report.md").write_text(report, encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
