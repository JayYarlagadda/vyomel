"""Episodes table (FR-507).

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "episodes",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(26),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "entity_ids",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column(
            "tools_used",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("embedding_model", sa.Text(), nullable=False),
    )
    op.create_index("ix_episodes_finished_at", "episodes", ["finished_at"])
    op.execute("CREATE INDEX ix_episodes_entity_ids ON episodes USING gin (entity_ids)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_episodes_entity_ids")
    op.drop_index("ix_episodes_finished_at", table_name="episodes")
    op.drop_table("episodes")
