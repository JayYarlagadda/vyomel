"""40 browser workflows for the M7 eval suite."""

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


def _open(page: str) -> WorkflowStep:
    return WorkflowStep("browser.open", {"url": f"fixture://{page}"})


_TITLE_EXPECT: dict[str, str] = {
    "job_board": "Job Board",
    "job_board_perturbed": "Job Board",
    "gradebook": "Gradebook",
    "form_app": "Application",
    "paginated": "Paginated",
}


def build_workflows() -> list[Workflow]:
    workflows: list[Workflow] = []
    pages = [
        ("job_board", "Apply", "button"),
        ("job_board_perturbed", "Apply", "button"),
        ("gradebook", "Export CSV", "button"),
        ("form_app", "Submit application", "button"),
        ("paginated", "Next page", "button"),
    ]
    for index, (page, label, role) in enumerate(pages):
        base = index * 8
        title_expect = _TITLE_EXPECT[page]
        workflows.extend(
            [
                Workflow(
                    f"w{base + 1:02d}_{page}_read",
                    (_open(page), WorkflowStep("browser.read_page", {})),
                    "title",
                    title_expect,
                ),
                Workflow(
                    f"w{base + 2:02d}_{page}_query",
                    (
                        _open(page),
                        WorkflowStep("browser.query", {"role": role, "name": label}),
                    ),
                    "name",
                    label,
                ),
                Workflow(
                    f"w{base + 3:02d}_{page}_click",
                    (
                        _open(page),
                        WorkflowStep("browser.click", {"role": role, "name": label}),
                    ),
                    "clicked",
                    True,
                ),
                Workflow(
                    f"w{base + 4:02d}_{page}_scroll",
                    (
                        _open(page),
                        WorkflowStep("browser.scroll", {"direction": "down", "amount": 200}),
                    ),
                    "scroll",
                    200,
                ),
                Workflow(
                    f"w{base + 5:02d}_{page}_screenshot",
                    (
                        _open(page),
                        WorkflowStep("browser.screenshot", {"filename": f"{page}.png"}),
                    ),
                    "bytes",
                    1,
                ),
            ]
        )
        if page.startswith("job_board"):
            workflows.append(
                Workflow(
                    f"w{base + 6:02d}_{page}_type",
                    (
                        _open(page),
                        WorkflowStep(
                            "browser.type", {"role": "textbox", "name": "Email", "text": "a@b.com"}
                        ),
                    ),
                    "typed",
                    "a@b.com",
                )
            )
            workflows.append(
                Workflow(
                    f"w{base + 7:02d}_{page}_download",
                    (
                        _open(page),
                        WorkflowStep(
                            "browser.download",
                            {"role": "link", "name": "Download resume", "filename": f"{page}.txt"},
                        ),
                    ),
                    "bytes",
                    1,
                )
            )
            workflows.append(
                Workflow(
                    f"w{base + 8:02d}_{page}_submit",
                    (
                        _open(page),
                        WorkflowStep("browser.submit", {"role": "button", "name": "Apply"}),
                    ),
                    "submitted",
                    True,
                )
            )
        elif page == "form_app":
            workflows.extend(
                [
                    Workflow(
                        f"w{base + 6:02d}_{page}_type",
                        (
                            _open(page),
                            WorkflowStep(
                                "browser.type",
                                {"role": "textbox", "name": "Full name", "text": "Vyomel"},
                            ),
                        ),
                        "typed",
                        "Vyomel",
                    ),
                    Workflow(
                        f"w{base + 7:02d}_{page}_select",
                        (
                            _open(page),
                            WorkflowStep(
                                "browser.select",
                                {"role": "combobox", "name": "Role", "value": "frontend"},
                            ),
                        ),
                        "selected",
                        "frontend",
                    ),
                    Workflow(
                        f"w{base + 8:02d}_{page}_submit",
                        (
                            _open(page),
                            WorkflowStep(
                                "browser.submit", {"role": "button", "name": "Submit application"}
                            ),
                        ),
                        "submitted",
                        True,
                    ),
                ]
            )
        else:
            workflows.extend(
                [
                    Workflow(
                        f"w{base + 6:02d}_{page}_download",
                        (
                            _open(page),
                            WorkflowStep(
                                "browser.download",
                                {
                                    "role": "link" if page == "paginated" else "button",
                                    "name": "Export page" if page == "paginated" else "Export CSV",
                                    "filename": f"{page}.txt",
                                },
                            ),
                        ),
                        "bytes",
                        1,
                    ),
                    Workflow(
                        f"w{base + 7:02d}_{page}_query_export",
                        (
                            _open(page),
                            WorkflowStep(
                                "browser.query",
                                {
                                    "role": "link" if page == "paginated" else "button",
                                    "name": "Export page" if page == "paginated" else "Export CSV",
                                },
                            ),
                        ),
                        "role",
                        "link" if page == "paginated" else "button",
                    ),
                    Workflow(
                        f"w{base + 8:02d}_{page}_read_again",
                        (
                            _open(page),
                            WorkflowStep("browser.read_page", {}),
                        ),
                        "url",
                        f"fixture://{page}",
                    ),
                ]
            )
    # Trim/pad to exactly 40 workflows
    return workflows[:40]
