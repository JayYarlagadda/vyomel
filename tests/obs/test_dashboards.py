"""Grafana dashboards (FR-805)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

DASHBOARDS = Path(__file__).resolve().parents[2] / "infra" / "grafana" / "dashboards"

EXPECTED = {
    "vyomel-task-health",
    "vyomel-tool-reliability",
    "vyomel-model-performance",
    "vyomel-security",
    "vyomel-memory",
    "vyomel-runtime",
}


@pytest.mark.req("FR-805")
def test_six_versioned_dashboards_are_valid_grafana_json() -> None:
    files = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in DASHBOARDS.glob("*.json")
    }
    assert set(files) == EXPECTED
    for uid, body in files.items():
        assert body["uid"] == uid
        assert body["title"].startswith("Vyomel ")
        assert body["schemaVersion"] >= 38
        assert body["panels"], uid
        exprs = [target["expr"] for panel in body["panels"] for target in panel.get("targets", [])]
        assert exprs, uid
        assert all("vyomel_" in expr for expr in exprs)
