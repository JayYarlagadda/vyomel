"""Helm chart structure checks (M13). No cluster required."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "infra" / "helm" / "vyomel"


def _helm_available() -> bool:
    try:
        subprocess.run(["helm", "version"], check=True, capture_output=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


@pytest.mark.skipif(not _helm_available(), reason="helm CLI not installed")
def test_helm_template_kind_profile_renders_core_workloads() -> None:
    proc = subprocess.run(
        [
            "helm",
            "template",
            "vyomel",
            str(CHART),
            "-f",
            str(CHART / "values-kind.yaml"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    docs = [d for d in yaml.safe_load_all(proc.stdout) if d]
    kinds = {(d.get("kind"), d["metadata"]["name"]) for d in docs}
    assert ("Deployment", "vyomel-api") in kinds
    assert ("Deployment", "vyomel-worker") in kinds
    assert ("Deployment", "vyomel-scheduler") in kinds
    assert ("StatefulSet", "vyomel-postgres") in kinds
    assert ("StatefulSet", "vyomel-redis") in kinds
    # kind profile disables HPA and vLLM
    assert not any(k == "HorizontalPodAutoscaler" for k, _ in kinds)
    assert not any(name.endswith("-vllm") for _, name in kinds)


def test_chart_files_exist() -> None:
    assert (CHART / "Chart.yaml").is_file()
    assert (CHART / "values.yaml").is_file()
    assert (CHART / "values-kind.yaml").is_file()
    for name in (
        "api.yaml",
        "worker.yaml",
        "scheduler.yaml",
        "postgres.yaml",
        "redis.yaml",
        "vllm.yaml",
    ):
        assert (CHART / "templates" / name).is_file()


def test_dockerfile_exists() -> None:
    assert (ROOT / "Dockerfile").is_file()
