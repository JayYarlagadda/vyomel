"""SQLAlchemy ORM models.

Normative schema: docs/03-DATA-MODEL.md. Enum labels are append-only.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Sequence,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from vyomel.core.ids import new_id
from vyomel.core.types import (
    ActionStatus,
    ApprovalStatus,
    Capability,
    EntityRelationType,
    EntityType,
    StepStatus,
    TaskOrigin,
    TaskStatus,
    VerifyOutcome,
)


class Base(DeclarativeBase):
    pass


class AsyncpgVector(VECTOR):
    """Pass lists through to asyncpg's binary vector codec.

    Upstream ``VECTOR.bind_processor`` stringifies to ``[0.1,0.2,...]``. That
    text then hits ``register_vector``, which expects a list and raises.
    """

    cache_ok = True

    def bind_processor(self, dialect: object) -> None:
        return None


def _pg_enum(enum_cls: type, name: str) -> Enum:
    # values_callable keeps Postgres labels identical to the Python values,
    # so a label can be appended without a Python-side rename.
    return Enum(
        enum_cls,
        name=name,
        native_enum=True,
        create_constraint=False,
        values_callable=lambda e: [m.value for m in e],
    )


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_intent: Mapped[str | None] = mapped_column(Text)

    status: Mapped[TaskStatus] = mapped_column(
        _pg_enum(TaskStatus, "task_status"), nullable=False, default=TaskStatus.CREATED
    )
    origin: Mapped[TaskOrigin] = mapped_column(
        _pg_enum(TaskOrigin, "task_origin"), nullable=False, default=TaskOrigin.API
    )
    capability_ceiling: Mapped[Capability] = mapped_column(
        _pg_enum(Capability, "capability"), nullable=False, default=Capability.L2
    )

    context_hints: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    plan_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    replan_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    token_budget: Mapped[int] = mapped_column(Integer, nullable=False, default=200_000)
    tokens_used: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    max_wall_clock_s: Mapped[int] = mapped_column(Integer, nullable=False, default=3_600)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    trace_id: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_tasks_status_created_at", "status", "created_at"),
        Index("ix_tasks_origin", "origin"),
        Index("ix_tasks_trace_id", "trace_id"),
    )

    steps: Mapped[list[Step]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="Step.ordinal"
    )

    def __repr__(self) -> str:
        return f"<Task {self.id} {self.status}>"


class Step(Base):
    __tablename__ = "steps"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[StepStatus] = mapped_column(
        _pg_enum(StepStatus, "step_status"), nullable=False, default=StepStatus.PLANNED
    )
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    depends_on: Mapped[list[str]] = mapped_column(
        ARRAY(String(26)), nullable=False, server_default="{}"
    )
    tolerates_unverified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    task: Mapped[Task] = relationship(back_populates="steps")
    actions: Mapped[list[Action]] = relationship(
        back_populates="step", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_steps_task_id", "task_id"),)

    def __repr__(self) -> str:
        return f"<Step {self.id} {self.status}>"


class StepEdge(Base):
    __tablename__ = "step_edges"

    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    from_step_id: Mapped[str] = mapped_column(
        ForeignKey("steps.id", ondelete="CASCADE"), primary_key=True
    )
    to_step_id: Mapped[str] = mapped_column(
        ForeignKey("steps.id", ondelete="CASCADE"), primary_key=True
    )
    plan_version: Mapped[int] = mapped_column(Integer, primary_key=True)

    __table_args__ = (CheckConstraint("from_step_id <> to_step_id", name="step_edges_no_self"),)


class Action(Base):
    __tablename__ = "actions"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    step_id: Mapped[str] = mapped_column(ForeignKey("steps.id", ondelete="CASCADE"), nullable=False)
    tool: Mapped[str] = mapped_column(Text, nullable=False)
    tool_version: Mapped[str] = mapped_column(String(32), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    preconditions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    postconditions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    capability_level: Mapped[Capability] = mapped_column(
        _pg_enum(Capability, "capability"), nullable=False, default=Capability.L0
    )
    reversible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    depends_on: Mapped[list[str]] = mapped_column(
        ARRAY(String(26)), nullable=False, server_default="{}"
    )
    status: Mapped[ActionStatus] = mapped_column(
        _pg_enum(ActionStatus, "action_status"), nullable=False, default=ActionStatus.PLANNED
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    timeout_s: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Backoff gate. Distinct from lease_until so the reaper SQL cannot
    # accidentally treat a waiting-to-retry READY row as an expired lease.
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    span_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    step: Mapped[Step] = relationship(back_populates="actions")

    __table_args__ = (
        Index("ix_actions_status_lease_until", "status", "lease_until"),
        Index("ix_actions_status_available_at", "status", "available_at"),
        Index("ix_actions_task_id_status", "task_id", "status"),
        Index("ix_actions_tool_status", "tool", "status"),
    )

    def __repr__(self) -> str:
        return f"<Action {self.id} {self.tool} {self.status}>"


class Verification(Base):
    """One postcondition check against one action (FR-401, FR-405).

    Evidence lives here, not only in the audit payload: the audit trail is
    summarized (hashes, outcomes) because it is permanent and a tool result can
    be a megabyte of file content. The row is the thing a reviewer inspects.
    """

    __tablename__ = "verifications"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    action_id: Mapped[str] = mapped_column(
        ForeignKey("actions.id", ondelete="CASCADE"), nullable=False
    )
    verifier: Mapped[str] = mapped_column(Text, nullable=False)
    expected: Mapped[Any] = mapped_column(JSONB, nullable=True)
    observed: Mapped[Any] = mapped_column(JSONB, nullable=True)
    outcome: Mapped[VerifyOutcome] = mapped_column(
        _pg_enum(VerifyOutcome, "verify_outcome"), nullable=False
    )
    observation_tier: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    evidence_ref: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_verifications_action_id", "action_id"),)

    def __repr__(self) -> str:
        return f"<Verification {self.id} {self.verifier} {self.outcome}>"


class SideEffectLedger(Base):
    """Reservation/record of an externally visible effect (FR-207)."""

    __tablename__ = "side_effect_ledger"

    idempotency_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    tool: Mapped[str] = mapped_column(Text, nullable=False)
    action_id: Mapped[str] = mapped_column(
        ForeignKey("actions.id", ondelete="CASCADE"), nullable=False
    )
    external_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Approval(Base):
    """A pending or decided human decision about one action (FR-303, FR-304).

    ``presented`` stores the exact payload rendered to the user. Auditing what
    the system *intended* to show would make the record useless in the case that
    matters: a rendering bug that hid the true target of an action.

    Three columns are additive to the sketch in docs/03-DATA-MODEL.md and are
    what make section 4.2 of the security document enforceable rather than
    aspirational:

    ``parameter_hash``
        Binds the approval to the exact parameters shown. Approving does not
        approve the action, it approves *this* invocation of it.
    ``consumed_at``
        Makes the approval single-use. A dispatch consumes it; a replay after a
        crash finds it spent and asks again.
    ``policy_rule_id`` / ``policy_hash``
        Attributes the gate to a specific rule in a specific policy version.
    """

    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    action_id: Mapped[str] = mapped_column(
        ForeignKey("actions.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    capability_level: Mapped[Capability] = mapped_column(
        _pg_enum(Capability, "capability"), nullable=False
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    presented: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    blast_radius: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[ApprovalStatus] = mapped_column(
        _pg_enum(ApprovalStatus, "approval_status"), nullable=False, default=ApprovalStatus.PENDING
    )
    parameter_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    modified_parameters: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    policy_rule_id: Mapped[str | None] = mapped_column(Text)
    policy_hash: Mapped[str | None] = mapped_column(String(64))
    decided_by: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # "One live approval per action": partial, so decided approvals remain
        # as history instead of being overwritten by the next request.
        Index(
            "uq_approvals_live_action",
            "action_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index("ix_approvals_status_expires_at", "status", "expires_at"),
        Index("ix_approvals_task_id", "task_id"),
    )

    def __repr__(self) -> str:
        return f"<Approval {self.id} {self.status} {self.capability_level}>"


AUDIT_ID_SEQ = Sequence("audit_log_id_seq")


class AuditLog(Base):
    """Append-only, hash-chained record of everything that mattered (FR-307).

    ``id`` is drawn from the sequence *before* insert so it can be covered by
    the hash. Chaining alone would let a row be renumbered; including the id
    means the position in the chain is signed too.

    There is deliberately no foreign key to ``tasks`` or ``actions``: the audit
    trail has to outlive the rows it describes, and a cascade delete from a task
    would silently rewrite history — which is exactly what the append-only
    trigger exists to prevent.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, AUDIT_ID_SEQ, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(26))
    action_id: Mapped[str | None] = mapped_column(String(26))
    capability_level: Mapped[Capability | None] = mapped_column(_pg_enum(Capability, "capability"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        Index("ix_audit_log_task_id", "task_id"),
        Index("ix_audit_log_action_id", "action_id"),
        Index("ix_audit_log_event_type", "event_type"),
    )

    def __repr__(self) -> str:
        return f"<AuditLog {self.id} {self.event_type}>"


class DeadLetter(Base):
    __tablename__ = "dead_letters"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    action_id: Mapped[str] = mapped_column(
        ForeignKey("actions.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Document(Base):
    """A local file ingested into semantic memory (docs/03 §4.3).

    Structured identity stays on this row. The embedding lives only on chunks
    (FR-504): the document is not a vector.
    """

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    entity_id: Mapped[str | None] = mapped_column(
        ForeignKey("entities.id", ondelete="SET NULL"), nullable=True
    )
    path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    mime: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    entity: Mapped[Entity | None] = relationship(back_populates="documents")


class DocumentChunk(Base):
    """One retrieval unit. Vector + tsvector; citations from the columns below."""

    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    page: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(AsyncpgVector(384), nullable=False)
    embedding_model: Mapped[str] = mapped_column(Text, nullable=False)

    document: Mapped[Document] = relationship(back_populates="chunks")

    __table_args__ = (Index("ix_document_chunks_document_id", "document_id"),)


class Entity(Base):
    """A node in the personal context graph (docs/03 §4.1)."""

    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    type: Mapped[EntityType] = mapped_column(_pg_enum(EntityType, "entity_type"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    salience: Mapped[float] = mapped_column(nullable=False, default=1.0)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    documents: Mapped[list[Document]] = relationship(back_populates="entity")
    outgoing_relations: Mapped[list[EntityRelation]] = relationship(
        back_populates="from_entity",
        foreign_keys="EntityRelation.from_id",
        cascade="all, delete-orphan",
    )
    incoming_relations: Mapped[list[EntityRelation]] = relationship(
        back_populates="to_entity",
        foreign_keys="EntityRelation.to_id",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_entities_name_tsv", text("to_tsvector('english', name)"), postgresql_using="gin"),
        Index("ix_entities_aliases", "aliases", postgresql_using="gin"),
    )


class EntityRelation(Base):
    """A typed edge in the context graph (docs/03 §4.2)."""

    __tablename__ = "entity_relations"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    from_id: Mapped[str] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    relation: Mapped[EntityRelationType] = mapped_column(
        _pg_enum(EntityRelationType, "entity_relation"), nullable=False
    )
    to_id: Mapped[str] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    confidence: Mapped[float] = mapped_column(nullable=False, default=1.0)
    evidence_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    from_entity: Mapped[Entity] = relationship(
        back_populates="outgoing_relations", foreign_keys=[from_id]
    )
    to_entity: Mapped[Entity] = relationship(
        back_populates="incoming_relations", foreign_keys=[to_id]
    )

    __table_args__ = (
        Index("ix_entity_relations_from_id", "from_id"),
        Index("ix_entity_relations_to_id", "to_id"),
        Index(
            "uq_entity_relations_from_relation_to",
            "from_id",
            "relation",
            "to_id",
            unique=True,
        ),
    )


class Episode(Base):
    """Compact record of a completed task (docs/03 §4.4, FR-507)."""

    __tablename__ = "episodes"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    entity_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    tools_used: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(AsyncpgVector(384), nullable=False)
    embedding_model: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_episodes_finished_at", "finished_at"),
        Index("ix_episodes_entity_ids", "entity_ids", postgresql_using="gin"),
    )


class ModelCall(Base):
    __tablename__ = "model_calls"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    action_id: Mapped[str | None] = mapped_column(
        ForeignKey("actions.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    ttft_ms: Mapped[float] = mapped_column(nullable=False)
    latency_ms: Mapped[float] = mapped_column(nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    sensitivity: Mapped[str] = mapped_column(Text, nullable=False, default="PUBLIC")
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_model_calls_task_id", "task_id"),
        Index("ix_model_calls_created_at", "created_at"),
    )


class Workflow(Base):
    """Learned or user-authored parameterized plan template (docs/03 §4.5)."""

    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(Text, nullable=False, default="learned")
    definition: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    parameters: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pattern_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="proposed")
    trust_level: Mapped[Capability] = mapped_column(
        _pg_enum(Capability, "capability"), nullable=False, default=Capability.L2
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_workflows_status", "status"),
        Index("ix_workflows_pattern_key", "pattern_key"),
        CheckConstraint(
            "trust_level IN ('L0','L1','L2')",
            name="workflows_trust_level_cap_l2",
        ),
    )


class WorkflowSuppression(Base):
    """Rejected pattern keys that must not be re-proposed (FR-903)."""

    __tablename__ = "workflow_suppressions"

    pattern_key: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
