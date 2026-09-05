# M14 media scenario S7 — 2026-09-04

Fixture clips (12). Concatenate, cut a 60s draft, mute profanity,
add sidecar captions, export.

## Results

| metric | value |
|---|---:|
| success | True |
| clips | 12 |
| source duration (s) | 66.0 |
| draft duration (s) | 60.0 |
| profanity segments muted | 2 |
| caption cues | 15 |
| steps | 21 |
| export CONFIRM | true |

Exit criterion (S7 end-to-end on the shared runtime) met on the fixture backend.

## Reproduce

```powershell
python evals/suites/media/run.py
pytest tests/tools/test_media.py
```
