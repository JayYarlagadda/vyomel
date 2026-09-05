"""Desktop actuation hierarchy (FR-605)."""

from __future__ import annotations

from pathlib import Path

import pytest

from vyomel.tools.desktop.fixture import load_fixture
from vyomel.tools.desktop.metrics import actuation_tier_distribution, reset_actuation_tiers
from vyomel.tools.desktop.resolve import resolve_element
from vyomel.tools.desktop.types import Target


@pytest.mark.req("FR-605")
def test_resolver_prefers_uia_over_automation_id() -> None:
    fixtures = Path(__file__).resolve().parents[2] / "vyomel" / "tools" / "desktop" / "fixtures"
    title, root = load_fixture(fixtures / "gradebook_perturbed.json")
    assert title == "Gradebook"
    element, tier = resolve_element(root, Target(role="Button", name="Export CSV"))
    assert tier == 2
    assert element.name == "Export CSV"


@pytest.mark.req("FR-605")
def test_resolver_uses_automation_id_when_name_missing() -> None:
    fixtures = Path(__file__).resolve().parents[2] / "vyomel" / "tools" / "desktop" / "fixtures"
    _, root = load_fixture(fixtures / "gradebook_perturbed.json")
    element, tier = resolve_element(root, Target(automation_id="export_btn_moved"))
    assert tier == 3
    assert element.automation_id == "export_btn_moved"


@pytest.mark.req("FR-605")
def test_resolver_uses_coordinates_as_last_resort() -> None:
    fixtures = Path(__file__).resolve().parents[2] / "vyomel" / "tools" / "desktop" / "fixtures"
    _, root = load_fixture(fixtures / "gradebook.json")
    reset_actuation_tiers()
    element, tier = resolve_element(root, Target(x=60, y=334))
    assert tier == 4
    assert element.name == "Export CSV"
    tiers = actuation_tier_distribution()
    assert tiers.get("4") == 1
    assert "2" not in tiers


@pytest.mark.req("FR-605")
def test_resolver_ref_is_tier_two() -> None:
    fixtures = Path(__file__).resolve().parents[2] / "vyomel" / "tools" / "desktop" / "fixtures"
    _, root = load_fixture(fixtures / "inventory.json")
    found, tier = resolve_element(root, Target(role="Button", name="Export report"))
    element, ref_tier = resolve_element(root, Target(ref=found.ref))
    assert tier == 2
    assert ref_tier == 2
    assert element.ref == found.ref
