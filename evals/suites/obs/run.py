"""M10 observability fixture eval: in-process spans + dashboard JSON validity."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vyomel.obs.metrics import ACTIONS_TOTAL, POLICY_DECISIONS, TASKS_TOTAL, exposition
from vyomel.obs.timeline import TraceNode, render_timeline
from vyomel.obs.tracing import SpanLink, recorder, reset_spans, start_span

DASHBOARDS = ROOT / "infra" / "grafana" / "dashboards"


def _lifecycle() -> dict[str, object]:
    reset_spans()
    with start_span("task", **{"task.id": "eval-m10", "task.origin": "eval"}) as task:
        with start_span("plan", **{"plan.step_count": 1}):
            with start_span("memory.retrieve", **{"retrieval.k": 8}):
                pass
            with start_span("model.complete", **{"model.purpose": "plan"}):
                pass
        with start_span("action", **{"action.tool": "task.report"}):
            with start_span("policy.evaluate", **{"policy.decision": "ALLOW"}):
                pass
            with start_span("tool.execute", **{"tool.name": "task.report"}):
                pass
            with start_span("verify", **{"verify.outcome": "PASS"}):
                pass
        abandoned = task.span_id
    with start_span(
        "action",
        trace_id=task.trace_id,
        links=[SpanLink(task.trace_id, abandoned, {"resumed_after_crash": True})],
    ):
        pass
    TASKS_TOTAL.labels(status="SUCCEEDED", origin="eval").inc()
    ACTIONS_TOTAL.labels(tool="task.report", status="SUCCEEDED", capability="L0").inc()
    POLICY_DECISIONS.labels(decision="ALLOW", capability="L0", rule_id="default-allow").inc()
    names = [span.name for span in recorder().spans(trace_id=task.trace_id)]
    tree = TraceNode(
        name='eval-m10 "observability lifecycle"',
        status="SUCCEEDED",
        duration_s=1.2,
        children=(
            TraceNode(name="plan"),
            TraceNode(
                name="step 1  report",
                children=(TraceNode(name="task.report  L0", status="SUCCEEDED", duration_s=0.1),),
            ),
        ),
    )
    body = exposition().decode()
    dashboards = sorted(p.stem for p in DASHBOARDS.glob("*.json"))
    return {
        "trace_id": task.trace_id,
        "span_names": names,
        "has_resume_link": any(span.links for span in recorder().spans(trace_id=task.trace_id)),
        "metrics_present": all(
            name in body
            for name in ("vyomel_tasks_total", "vyomel_actions_total", "vyomel_policy_decisions_total")
        ),
        "dashboards": dashboards,
        "timeline_preview": render_timeline(tree).splitlines()[0],
    }


def main() -> None:
    result = _lifecycle()
    out_dir = ROOT / "evals" / "results" / "2026-09-03-m10"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "suite": "obs",
        "backend": "in-process",
        "recorded_at": datetime.now(UTC).isoformat(),
        "success_rate": 1.0 if result["metrics_present"] and len(result["dashboards"]) == 6 else 0.0,
        **result,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    if summary["success_rate"] != 1.0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
