# M7 browser workflow eval — 2026-09-02

40 workflows against local HTML fixtures (`vyomel/tools/browser/fixtures/`).
Includes DOM-perturbation coverage via `job_board_perturbed.html` (changed CSS
classes, stable `aria-label` targets).

## Results

| backend | workflows | success_rate | actuation tier 2/3/4 |
|---|---:|---:|---|
| fixture | 40 | **1.000** | 120 / 80 / 40 |

Exit criterion (≥ 80 %) met.

## Reproduce

```powershell
python evals/suites/browser/run.py --backend fixture
pytest tests/tools/test_browser.py
```

Playwright backend (optional):

```powershell
pip install vyomel[browser]
playwright install chromium
python evals/suites/browser/run.py --backend playwright
```
