"""50 desktop workflows for the M8 eval suite."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    tool: str
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Workflow:
    name: str
    steps: tuple[WorkflowStep, ...]
    expect_field: str
    expect_value: Any


def _open(app: str) -> WorkflowStep:
    return WorkflowStep("app.open", {"target": f"fixture://{app}"})


_APP_CONFIG: dict[str, dict[str, str]] = {
    "gradebook": {
        "title": "Gradebook",
        "export_btn": "Export CSV",
        "search_field": "Search students",
        "filter_btn": "Apply filter",
        "save_btn": "Save",
        "export_id": "btn_export",
    },
    "gradebook_perturbed": {
        "title": "Gradebook",
        "export_btn": "Export CSV",
        "search_field": "Search students",
        "filter_btn": "Apply filter",
        "save_btn": "Save",
        "export_id": "export_btn_moved",
    },
    "student_form": {
        "title": "Student Registration",
        "submit_btn": "Submit registration",
        "name_field": "Full name",
        "password_field": "Password",
        "name_id": "field_name",
    },
    "inventory": {
        "title": "Inventory",
        "export_btn": "Export report",
        "search_field": "SKU lookup",
        "search_btn": "Search",
        "export_id": "btn_export",
    },
    "task_list": {
        "title": "Task Manager",
        "add_btn": "Add task",
        "task_field": "New task",
        "complete_btn": "Mark complete",
        "save_btn": "Save",
        "add_id": "btn_add",
    },
}


def _primary_button(cfg: dict[str, str]) -> str:
    return cfg.get("export_btn") or cfg.get("submit_btn") or cfg["add_btn"]


def _primary_field(cfg: dict[str, str]) -> str:
    return cfg.get("search_field") or cfg.get("name_field") or cfg["task_field"]


def _primary_id(cfg: dict[str, str]) -> str:
    return cfg.get("export_id") or cfg.get("name_id") or cfg["add_id"]


def build_workflows() -> list[Workflow]:
    workflows: list[Workflow] = []
    for app, cfg in _APP_CONFIG.items():
        base = len(workflows)
        title = cfg["title"]
        workflows.extend(
            [
                Workflow(
                    f"w{base + 1:02d}_{app}_list_windows",
                    (_open(app), WorkflowStep("desktop.list_windows", {})),
                    "windows",
                    title,
                ),
                Workflow(
                    f"w{base + 2:02d}_{app}_read_tree",
                    (_open(app), WorkflowStep("desktop.read_tree", {})),
                    "title",
                    title,
                ),
                Workflow(
                    f"w{base + 3:02d}_{app}_find_button",
                    (
                        _open(app),
                        WorkflowStep(
                            "desktop.find_element",
                            {
                                "role": "Button",
                                "name": _primary_button(cfg),
                            },
                        ),
                    ),
                    "role",
                    "Button",
                ),
                Workflow(
                    f"w{base + 4:02d}_{app}_click_export",
                    (
                        _open(app),
                        WorkflowStep(
                            "desktop.click_element",
                            {
                                "role": "Button",
                                "name": _primary_button(cfg),
                            },
                        ),
                    ),
                    "clicked",
                    True,
                ),
                Workflow(
                    f"w{base + 5:02d}_{app}_set_field",
                    (
                        _open(app),
                        WorkflowStep(
                            "desktop.set_field",
                            {
                                "role": "Edit",
                                "name": _primary_field(cfg),
                                "value": "test-value",
                            },
                        ),
                    ),
                    "value",
                    "test-value",
                ),
                Workflow(
                    f"w{base + 6:02d}_{app}_scroll",
                    (
                        _open(app),
                        WorkflowStep("desktop.scroll", {"direction": "down", "amount": 150}),
                    ),
                    "scroll",
                    150,
                ),
                Workflow(
                    f"w{base + 7:02d}_{app}_key_save",
                    (
                        _open(app),
                        WorkflowStep("desktop.key", {"keys": "Ctrl+S"}),
                    ),
                    "status",
                    "saved",
                ),
                Workflow(
                    f"w{base + 8:02d}_{app}_find_by_id",
                    (
                        _open(app),
                        WorkflowStep(
                            "desktop.find_element",
                            {"automation_id": _primary_id(cfg)},
                        ),
                    ),
                    "automation_id",
                    _primary_id(cfg),
                ),
                Workflow(
                    f"w{base + 9:02d}_{app}_type_text",
                    (
                        _open(app),
                        WorkflowStep(
                            "desktop.type_text",
                            {
                                "role": "Edit",
                                "name": _primary_field(cfg),
                                "text": "typed",
                            },
                        ),
                    ),
                    "typed",
                    "typed",
                ),
                Workflow(
                    f"w{base + 10:02d}_{app}_focus",
                    (
                        _open(app),
                        WorkflowStep("app.focus", {"title": title}),
                    ),
                    "focused",
                    True,
                ),
            ]
        )
    # Replace three workflows with coordinate clicks to exercise tier 4 under the 30% cap.
    workflows[3] = Workflow(
        "w04_gradebook_click_xy",
        (
            _open("gradebook"),
            WorkflowStep(
                "desktop.click_xy",
                {"x": 60, "y": 334, "evidence_filename": "gradebook_click.png"},
            ),
        ),
        "clicked",
        True,
    )
    workflows[23] = Workflow(
        "w24_inventory_click_xy",
        (
            _open("inventory"),
            WorkflowStep(
                "desktop.click_xy",
                {"x": 220, "y": 294, "evidence_filename": "inventory_click.png"},
            ),
        ),
        "clicked",
        True,
    )
    workflows[43] = Workflow(
        "w44_task_list_click_xy",
        (
            _open("task_list"),
            WorkflowStep(
                "desktop.click_xy",
                {"x": 50, "y": 314, "evidence_filename": "task_click.png"},
            ),
        ),
        "clicked",
        True,
    )
    return workflows[:50]


@dataclass(frozen=True, slots=True)
class VerificationFault:
    name: str
    steps: tuple[WorkflowStep, ...]
    field: str
    correct_value: Any


def verification_fault_workflows() -> list[VerificationFault]:
    """Injected wrong-value writes — a verifier expecting ``correct_value`` must fail."""
    return [
        VerificationFault(
            "vf_gradebook_set_field",
            (
                _open("gradebook"),
                WorkflowStep(
                    "desktop.set_field",
                    {"role": "Edit", "name": "Search students", "value": "wrong"},
                ),
            ),
            "value",
            "expected-correct",
        ),
        VerificationFault(
            "vf_student_form_type",
            (
                _open("student_form"),
                WorkflowStep(
                    "desktop.type_text",
                    {"role": "Edit", "name": "Full name", "text": "bad"},
                ),
            ),
            "typed",
            "expected-good",
        ),
    ]
