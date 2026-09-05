# M9 API scenario S3 — 2026-09-03

Fixture Gmail/Calendar world. Find yesterday's interview email, list the interview day, find two free 60-minute slots, create both events with the interviewer as attendee.

## Results

| metric | value |
|---|---:|
| success | true |
| L3 actions | 2 |
| L3 CONFIRM | 2 |
| L3 auto-ALLOW | 0 |

Exit criterion (S3 end-to-end with confirm gating on every L3 action) met.

## Reproduce

```powershell
python evals/suites/api/run.py
pytest tests/tools/test_oauth.py tests/tools/test_api_tools.py
```
