"""Post-action verification records.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-28

docs/03-DATA-MODEL.md section 3.5. Each row is one postcondition check against
one action. Evidence lives here because the audit payload is summarized: audit
rows are permanent and a tool result can be a megabyte of file content.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VERIFY_OUTCOME = ("PASS", "FAIL", "NO_METHOD")


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(*VERIFY_OUTCOME, name="verify_outcome").create(bind, checkfirst=True)

    op.create_table(
        "verifications",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "action_id",
            sa.String(26),
            sa.ForeignKey("actions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("verifier", sa.Text(), nullable=False),
        sa.Column("expected", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("observed", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "outcome",
            postgresql.ENUM(*VERIFY_OUTCOME, name="verify_outcome", create_type=False),
            nullable=False,
        ),
        sa.Column("observation_tier", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("evidence_ref", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_verifications_action_id", "verifications", ["action_id"])


def downgrade() -> None:
    op.drop_index("ix_verifications_action_id", table_name="verifications")
    op.drop_table("verifications")
    bind = op.get_bind()
    postgresql.ENUM(name="verify_outcome").drop(bind, checkfirst=True)
