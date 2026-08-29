"""Execution tables: steps, step_edges, actions, side_effect_ledger, dead_letters.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-28

Adds the durable-execution schema from docs/03-DATA-MODEL.md section 3.
``available_at`` is the one additive column not in the original table sketch:
backoff needs a timestamp the reaper SQL will not mistake for a lease.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STEP_STATUS = ("PLANNED", "READY", "RUNNING", "BLOCKED", "SUCCEEDED", "FAILED", "SKIPPED")
ACTION_STATUS = (
    "PLANNED",
    "READY",
    "DISPATCHED",
    "RUNNING",
    "WAITING_FOR_USER",
    "SUCCEEDED",
    "UNVERIFIED",
    "FAILED",
    "ROLLED_BACK",
    "CANCELLED",
)
CAPABILITY = ("L0", "L1", "L2", "L3", "L4")


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(*STEP_STATUS, name="step_status").create(bind, checkfirst=True)
    postgresql.ENUM(*ACTION_STATUS, name="action_status").create(bind, checkfirst=True)

    op.create_table(
        "steps",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(26),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("intent", sa.Text(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(*STEP_STATUS, name="step_status", create_type=False),
            nullable=False,
            server_default="PLANNED",
        ),
        sa.Column("plan_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "depends_on",
            postgresql.ARRAY(sa.String(26)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("tolerates_unverified", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_steps_task_id", "steps", ["task_id"])

    op.create_table(
        "step_edges",
        sa.Column(
            "task_id",
            sa.String(26),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "from_step_id",
            sa.String(26),
            sa.ForeignKey("steps.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "to_step_id",
            sa.String(26),
            sa.ForeignKey("steps.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("plan_version", sa.Integer(), primary_key=True),
        sa.CheckConstraint("from_step_id <> to_step_id", name="step_edges_no_self"),
    )

    op.create_table(
        "actions",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(26),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "step_id",
            sa.String(26),
            sa.ForeignKey("steps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("tool_version", sa.String(32), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "preconditions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "postconditions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "capability_level",
            postgresql.ENUM(*CAPABILITY, name="capability", create_type=False),
            nullable=False,
            server_default="L0",
        ),
        sa.Column("reversible", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "depends_on",
            postgresql.ARRAY(sa.String(26)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "status",
            postgresql.ENUM(*ACTION_STATUS, name="action_status", create_type=False),
            nullable=False,
            server_default="PLANNED",
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("timeout_s", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("span_id", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_actions_status_lease_until", "actions", ["status", "lease_until"])
    op.create_index("ix_actions_status_available_at", "actions", ["status", "available_at"])
    op.create_index("ix_actions_task_id_status", "actions", ["task_id", "status"])
    op.create_index("ix_actions_tool_status", "actions", ["tool", "status"])

    op.create_table(
        "side_effect_ledger",
        sa.Column("idempotency_key", sa.String(64), primary_key=True),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column(
            "action_id",
            sa.String(26),
            sa.ForeignKey("actions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_ref", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "dead_letters",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "action_id",
            sa.String(26),
            sa.ForeignKey("actions.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "context",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("dead_letters")
    op.drop_table("side_effect_ledger")
    op.drop_index("ix_actions_tool_status", table_name="actions")
    op.drop_index("ix_actions_task_id_status", table_name="actions")
    op.drop_index("ix_actions_status_available_at", table_name="actions")
    op.drop_index("ix_actions_status_lease_until", table_name="actions")
    op.drop_table("actions")
    op.drop_table("step_edges")
    op.drop_index("ix_steps_task_id", table_name="steps")
    op.drop_table("steps")
    bind = op.get_bind()
    postgresql.ENUM(name="action_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="step_status").drop(bind, checkfirst=True)
