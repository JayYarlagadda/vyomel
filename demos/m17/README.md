# M17 — Gym / multimodal (S8)

Fixture camera scene → equipment detections → today's session from personal history.
Wearable client uses the same `/v1` HTTP API (no parallel runtime).

```powershell
python demos/m17/run_demo.py
pytest tests/perception tests/clients/test_wearable.py
python evals/suites/gym/run.py
```
