# M16 voice — 2026-09-04

Fixture STT/TTS session: wake-word gate, utterance capture, speak, barge-in.

## Results

| metric | value |
|---|---:|
| success | True |
| barge_in_count | 1 |
| speech_bytes | 98 |
| backend | fixture |

## Reproduce

```powershell
python evals/suites/voice/run.py
pytest tests/voice
```
