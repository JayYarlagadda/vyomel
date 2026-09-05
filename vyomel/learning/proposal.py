"""Turn frequent sequences into parameterized workflow proposals (FR-902)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from vyomel.core.ids import new_id
from vyomel.core.types import Capability
from vyomel.learning.mining import FrequentSequence
from vyomel.learning.signatures import ObservedAction, normalize_action


class WorkflowStepTemplate(BaseModel):
    alias: str
    tool: str
    # Concrete literals stay; varying values become ``{"$param": "name"}``.
    parameters: dict[str, Any] = Field(default_factory=dict)


class WorkflowParameter(BaseModel):
    name: str
    description: str = ""
    example: Any = None


class WorkflowProposal(BaseModel):
    id: str
    name: str
    description: str
    source: str = "learned"
    definition: list[WorkflowStepTemplate]
    parameters: list[WorkflowParameter]
    occurrence_count: int
    trust_level: Capability = Capability.L2
    status: str = "proposed"  # proposed | accepted | rejected
    pattern_key: str
    supporting_task_ids: list[str] = Field(default_factory=list)


def _occurrences_for_pattern(
    pattern: FrequentSequence,
    corpus: list[tuple[str, list[ObservedAction]]],
) -> list[list[ObservedAction]]:
    """Collect concrete action windows that match the signature pattern."""
    wanted = [sig.key() for sig in pattern.signatures]
    length = len(wanted)
    found: list[list[ObservedAction]] = []
    for task_id, actions in corpus:
        if task_id not in pattern.task_ids:
            continue
        sigs = [normalize_action(a.tool, a.parameters).key() for a in actions]
        for start in range(0, len(sigs) - length + 1):
            if sigs[start : start + length] == wanted:
                found.append(actions[start : start + length])
                break
    return found


def _generalize_parameters(
    windows: list[list[ObservedAction]],
) -> tuple[list[WorkflowStepTemplate], list[WorkflowParameter]]:
    if not windows:
        return [], []
    length = len(windows[0])
    params: list[WorkflowParameter] = []
    used_names: set[str] = set()
    steps: list[WorkflowStepTemplate] = []

    for idx in range(length):
        tool = windows[0][idx].tool
        keys = sorted({k for window in windows for k in window[idx].parameters})
        templated: dict[str, Any] = {}
        for key in keys:
            values = [window[idx].parameters.get(key) for window in windows]
            # Present in every occurrence?
            if any(key not in window[idx].parameters for window in windows):
                continue
            if all(v == values[0] for v in values):
                templated[key] = values[0]
            else:
                base = f"step{idx + 1}_{key}"
                name = base
                n = 2
                while name in used_names:
                    name = f"{base}_{n}"
                    n += 1
                used_names.add(name)
                templated[key] = {"$param": name}
                params.append(
                    WorkflowParameter(
                        name=name,
                        description=f"{tool}.{key}",
                        example=values[0],
                    )
                )
        steps.append(
            WorkflowStepTemplate(
                alias=f"a{idx + 1}",
                tool=tool,
                parameters=templated,
            )
        )
    return steps, params


def _name_for(pattern: FrequentSequence) -> str:
    tools = [sig.tool.split(".")[-1] for sig in pattern.signatures]
    return "then_".join(tools[:4])


def propose_workflows(
    patterns: list[FrequentSequence],
    corpus: list[tuple[str, list[ObservedAction]]],
    *,
    min_support: int = 3,
    tool_capabilities: dict[str, Capability] | None = None,
) -> list[WorkflowProposal]:
    """Build proposals for patterns that meet the recurrence threshold."""
    caps = tool_capabilities or {}
    proposals: list[WorkflowProposal] = []
    for pattern in patterns:
        if pattern.support < min_support or pattern.length < 3:
            continue
        windows = _occurrences_for_pattern(pattern, corpus)
        if len(windows) < min_support:
            continue
        definition, parameters = _generalize_parameters(windows)
        max_cap = Capability.L0
        for step in definition:
            step_cap = caps.get(step.tool, Capability.L2)
            if step_cap > max_cap:
                max_cap = step_cap
        # FR-310 / docs/08: learned trust is capped at L2.
        trust = max_cap if max_cap <= Capability.L2 else Capability.L2
        proposals.append(
            WorkflowProposal(
                id=new_id(),
                name=_name_for(pattern),
                description=(f"Learned from {pattern.support} tasks: {pattern.pattern_key()}"),
                definition=definition,
                parameters=parameters,
                occurrence_count=pattern.support,
                trust_level=trust,
                status="proposed",
                pattern_key=pattern.pattern_key(),
                supporting_task_ids=list(pattern.task_ids),
            )
        )
    return proposals


def bind_parameters(proposal: WorkflowProposal, values: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand a proposal into concrete tool calls."""
    missing = [p.name for p in proposal.parameters if p.name not in values]
    if missing:
        raise ValueError(f"missing workflow parameters: {', '.join(missing)}")

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if set(node.keys()) == {"$param"}:
                return values[str(node["$param"])]
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return [
        {
            "alias": step.alias,
            "tool": step.tool,
            "parameters": resolve(step.parameters),
        }
        for step in proposal.definition
    ]
