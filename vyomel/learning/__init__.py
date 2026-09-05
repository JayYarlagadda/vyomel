"""Workflow learning package (FR-901-903)."""

from vyomel.learning.mining import FrequentSequence, mine_frequent_sequences
from vyomel.learning.proposal import WorkflowProposal, bind_parameters, propose_workflows
from vyomel.learning.service import actions_from_records, mine_and_propose
from vyomel.learning.signatures import ActionSignature, ObservedAction, normalize_action
from vyomel.learning.store import (
    MemoryWorkflowStore,
    accept_workflow,
    expand_workflow,
    get_workflow_store,
    reject_workflow,
    require_accepted,
    reset_workflow_store,
)

__all__ = [
    "ActionSignature",
    "FrequentSequence",
    "MemoryWorkflowStore",
    "ObservedAction",
    "WorkflowProposal",
    "accept_workflow",
    "actions_from_records",
    "bind_parameters",
    "expand_workflow",
    "get_workflow_store",
    "mine_and_propose",
    "mine_frequent_sequences",
    "normalize_action",
    "propose_workflows",
    "reject_workflow",
    "require_accepted",
    "reset_workflow_store",
]
