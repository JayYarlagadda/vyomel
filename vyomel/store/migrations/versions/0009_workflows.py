"""Workflows table (docs/03 §4.5, FR-901–903 / FR-310).

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.Text(), nullable=False, server_default="learned"),
        sa.Column("definition", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("parameters", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pattern_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="proposed"),
        sa.Column(
            "trust_level",
            postgresql.ENUM("L0", "L1", "L2", "L3", "L4", name="capability", create_type=False),
            nullable=False,
            server_default="L2",
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "trust_level IN ('L0','L1','L2')",
            name="workflows_trust_level_cap_l2",
        ),
    )
    op.create_index("ix_workflows_status", "workflows", ["status"])
    op.create_index("ix_workflows_pattern_key", "workflows", ["pattern_key"])
    op.create_table(
        "workflow_suppressions",
        sa.Column("pattern_key", sa.Text(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("workflow_suppressions")
    op.drop_index("ix_workflows_pattern_key", table_name="workflows")
    op.drop_index("ix_workflows_status", table_name="workflows")
    op.drop_table("workflows")
