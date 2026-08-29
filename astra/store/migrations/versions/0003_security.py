"""Approvals and the append-only audit log.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-28

docs/03-DATA-MODEL.md sections 3.4 and 3.6. Two things here are enforcement,
not bookkeeping:

- ``uq_approvals_live_action`` is a *partial* unique index. "One live approval
  per action" must not be implemented as one approval ever, or re-gating a
  modified action would have to destroy the record of the first decision.
- ``audit_log`` gets a ``BEFORE UPDATE OR DELETE`` trigger. The hash chain makes
  tampering detectable; the trigger makes it fail. Detection alone is not a
  control if the attacker is the process holding the credentials.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPROVAL_STATUS = ("PENDING", "APPROVED", "MODIFIED", "REJECTED", "EXPIRED")
CAPABILITY = ("L0", "L1", "L2", "L3", "L4")

_IMMUTABLE_FN = """
CREATE OR REPLACE FUNCTION astra_audit_log_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only; % is not permitted', TG_OP
        USING ERRCODE = 'check_violation';
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(*APPROVAL_STATUS, name="approval_status").create(bind, checkfirst=True)

    op.create_table(
        "approvals",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "action_id",
            sa.String(26),
            sa.ForeignKey("actions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.String(26),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "capability_level",
            postgresql.ENUM(*CAPABILITY, name="capability", create_type=False),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "presented",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "blast_radius",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "status",
            postgresql.ENUM(*APPROVAL_STATUS, name="approval_status", create_type=False),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("parameter_hash", sa.String(64), nullable=False),
        sa.Column("modified_parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("policy_rule_id", sa.Text(), nullable=True),
        sa.Column("policy_hash", sa.String(64), nullable=True),
        sa.Column("decided_by", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "uq_approvals_live_action",
        "approvals",
        ["action_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index("ix_approvals_status_expires_at", "approvals", ["status", "expires_at"])
    op.create_index("ix_approvals_task_id", "approvals", ["task_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("task_id", sa.String(26), nullable=True),
        sa.Column("action_id", sa.String(26), nullable=True),
        sa.Column(
            "capability_level",
            postgresql.ENUM(*CAPABILITY, name="capability", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column("prev_hash", sa.String(64), nullable=True),
        sa.Column("hash", sa.String(64), nullable=False),
    )
    op.create_index("ix_audit_log_task_id", "audit_log", ["task_id"])
    op.create_index("ix_audit_log_action_id", "audit_log", ["action_id"])
    op.create_index("ix_audit_log_event_type", "audit_log", ["event_type"])

    op.execute(_IMMUTABLE_FN)
    op.execute(
        "CREATE TRIGGER audit_log_immutable "
        "BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION astra_audit_log_immutable()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_immutable ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS astra_audit_log_immutable()")
    op.drop_index("ix_audit_log_event_type", table_name="audit_log")
    op.drop_index("ix_audit_log_action_id", table_name="audit_log")
    op.drop_index("ix_audit_log_task_id", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_approvals_task_id", table_name="approvals")
    op.drop_index("ix_approvals_status_expires_at", table_name="approvals")
    op.drop_index("uq_approvals_live_action", table_name="approvals")
    op.drop_table("approvals")
    bind = op.get_bind()
    postgresql.ENUM(name="approval_status").drop(bind, checkfirst=True)
