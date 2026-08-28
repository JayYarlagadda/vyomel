"""Initial schema: extensions, shared enums, tasks table.

Revision ID: 0001
Revises:
Create Date: 2026-08-28

Enums are created here rather than implicitly by the ORM so that later tables
(steps, actions, approvals) can reference the same Postgres types. Per
docs/03-DATA-MODEL.md section 7, enum labels are append-only forever.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CAPABILITY = ("L0", "L1", "L2", "L3", "L4")
TASK_STATUS = (
    "CREATED",
    "PLANNING",
    "READY",
    "RUNNING",
    "WAITING_FOR_USER",
    "PAUSED",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "NEEDS_HUMAN",
)
TASK_ORIGIN = ("cli", "api", "voice", "schedule", "workflow")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gin")

    capability = postgresql.ENUM(*CAPABILITY, name="capability")
    task_status = postgresql.ENUM(*TASK_STATUS, name="task_status")
    task_origin = postgresql.ENUM(*TASK_ORIGIN, name="task_origin")
    bind = op.get_bind()
    capability.create(bind, checkfirst=True)
    task_status.create(bind, checkfirst=True)
    task_origin.create(bind, checkfirst=True)

    op.create_table(
        "tasks",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("normalized_intent", sa.Text(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(*TASK_STATUS, name="task_status", create_type=False),
            nullable=False,
            server_default="CREATED",
        ),
        sa.Column(
            "origin",
            postgresql.ENUM(*TASK_ORIGIN, name="task_origin", create_type=False),
            nullable=False,
            server_default="api",
        ),
        sa.Column(
            "capability_ceiling",
            postgresql.ENUM(*CAPABILITY, name="capability", create_type=False),
            nullable=False,
            server_default="L2",
        ),
        sa.Column(
            "context_hints",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("plan_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("replan_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_budget", sa.Integer(), nullable=False, server_default="200000"),
        sa.Column("tokens_used", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("max_wall_clock_s", sa.Integer(), nullable=False, server_default="3600"),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_tasks_status_created_at", "tasks", ["status", "created_at"])
    op.create_index("ix_tasks_origin", "tasks", ["origin"])
    op.create_index("ix_tasks_trace_id", "tasks", ["trace_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_trace_id", table_name="tasks")
    op.drop_index("ix_tasks_origin", table_name="tasks")
    op.drop_index("ix_tasks_status_created_at", table_name="tasks")
    op.drop_table("tasks")

    bind = op.get_bind()
    for name in ("task_origin", "task_status", "capability"):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
