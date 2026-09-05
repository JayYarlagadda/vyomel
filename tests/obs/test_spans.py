"""In-process tracing (FR-801)."""

from __future__ import annotations

import pytest

from vyomel.obs.timeline import TraceNode, render_timeline
from vyomel.obs.tracing import (
    SpanLink,
    format_traceparent,
    parse_traceparent,
    recorder,
    reset_spans,
    start_span,
)


@pytest.fixture(autouse=True)
def _clean_spans() -> None:
    reset_spans()
    yield
    reset_spans()


@pytest.mark.req("FR-801")
def test_nested_spans_share_a_trace() -> None:
    with (
        start_span("task", **{"task.id": "t1"}) as root,
        start_span("action", **{"action.id": "a1"}) as child,
        start_span("tool.execute", **{"tool.name": "fs.list_dir"}),
    ):
        assert child.trace_id == root.trace_id
        assert child.parent_span_id == root.span_id
    names = [span.name for span in recorder().spans(trace_id=root.trace_id)]
    assert names == ["tool.execute", "action", "task"]


@pytest.mark.req("FR-801")
def test_resume_after_crash_links_the_abandoned_span() -> None:
    with start_span("action", trace_id="a" * 32) as first:
        abandoned = first.span_id
    with start_span(
        "action",
        trace_id="a" * 32,
        links=[SpanLink("a" * 32, abandoned, {"resumed_after_crash": True})],
    ) as resumed:
        assert resumed.links[0].span_id == abandoned
        assert resumed.links[0].attributes["resumed_after_crash"] is True
    assert len(recorder().spans(trace_id="a" * 32)) == 2


@pytest.mark.req("FR-801")
def test_traceparent_round_trip() -> None:
    header = format_traceparent("b" * 32, "c" * 16)
    assert header == f"00-{('b' * 32)}-{('c' * 16)}-01"
    assert parse_traceparent(header) == ("b" * 32, "c" * 16)
    assert parse_traceparent("not-a-header") is None


@pytest.mark.req("FR-801")
def test_span_attributes_are_redacted() -> None:
    with start_span(
        "tool.execute", **{"token": "supersecret-value", "tool.name": "http.get"}
    ) as span:
        pass
    assert span.attributes["token"] == "***REDACTED***"
    assert span.attributes["tool.name"] == "http.get"


def test_timeline_render_matches_the_docs_shape() -> None:
    tree = TraceNode(
        name='01J8 "grade submission 482"',
        status="SUCCEEDED",
        duration_s=12.8,
        children=(
            TraceNode(name="step 1  read rubric", children=(TraceNode(name="fs.read_file  L0"),)),
        ),
    )
    rendered = render_timeline(tree)
    assert "SUCCEEDED" in rendered
    assert "step 1" in rendered
    assert "fs.read_file" in rendered
