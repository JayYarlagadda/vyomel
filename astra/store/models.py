"""SQLAlchemy ORM models.

Only the tables required by M0 live here. Steps, actions, approvals,
verifications, audit, and the memory tables land in M1-M4 following
docs/03-DATA-MODEL.md, which is the normative schema reference.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, DateTime, Enum, Index, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from astra.core.ids import new_id
from astra.core.types import Capability, TaskOrigin, TaskStatus


class Base(DeclarativeBase):
    pass


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

    def __repr__(self) -> str:
        return f"<Task {self.id} {self.status}>"
