"""Model call accounting table (FR-704).

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_calls",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("task_id", sa.String(26), sa.ForeignKey("tasks.id", ondelete="SET NULL")),
        sa.Column("action_id", sa.String(26), sa.ForeignKey("actions.id", ondelete="SET NULL")),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("ttft_ms", sa.Float(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("sensitivity", sa.Text(), nullable=False, server_default="PUBLIC"),
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("prompt_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_model_calls_task_id", "model_calls", ["task_id"])
    op.create_index("ix_model_calls_created_at", "model_calls", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_model_calls_created_at", table_name="model_calls")
    op.drop_index("ix_model_calls_task_id", table_name="model_calls")
    op.drop_table("model_calls")
