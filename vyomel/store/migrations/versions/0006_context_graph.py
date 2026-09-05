"""Context graph: entities, relations, document.entity_id (FR-502).

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENTITY_TYPE = (
    "person",
    "project",
    "document",
    "application",
    "task_ref",
    "preference",
    "workflow",
    "organization",
    "event",
    "place",
)
ENTITY_RELATION = (
    "belongs_to",
    "authored_by",
    "mentions",
    "depends_on",
    "scheduled_for",
    "located_at",
    "related_to",
    "derived_from",
)


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(*ENTITY_TYPE, name="entity_type").create(bind, checkfirst=True)
    postgresql.ENUM(*ENTITY_RELATION, name="entity_relation").create(bind, checkfirst=True)

    op.create_table(
        "entities",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "type",
            postgresql.ENUM(*ENTITY_TYPE, name="entity_type", create_type=False),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "aliases",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("salience", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        "CREATE INDEX ix_entities_name_tsv ON entities USING gin (to_tsvector('english', name))"
    )
    op.create_index("ix_entities_aliases", "entities", ["aliases"], postgresql_using="gin")

    op.create_table(
        "entity_relations",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "from_id",
            sa.String(26),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "relation",
            postgresql.ENUM(*ENTITY_RELATION, name="entity_relation", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "to_id",
            sa.String(26),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("evidence_ref", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "from_id", "relation", "to_id", name="uq_entity_relations_from_relation_to"
        ),
    )
    op.create_index("ix_entity_relations_from_id", "entity_relations", ["from_id"])
    op.create_index("ix_entity_relations_to_id", "entity_relations", ["to_id"])

    op.add_column("documents", sa.Column("entity_id", sa.String(26), nullable=True))
    op.create_foreign_key(
        "fk_documents_entity_id_entities",
        "documents",
        "entities",
        ["entity_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_documents_entity_id", "documents", ["entity_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_entity_id", table_name="documents")
    op.drop_constraint("fk_documents_entity_id_entities", "documents", type_="foreignkey")
    op.drop_column("documents", "entity_id")
    op.drop_index("ix_entity_relations_to_id", table_name="entity_relations")
    op.drop_index("ix_entity_relations_from_id", table_name="entity_relations")
    op.drop_table("entity_relations")
    op.drop_index("ix_entities_aliases", table_name="entities")
    op.execute("DROP INDEX IF EXISTS ix_entities_name_tsv")
    op.drop_table("entities")
    bind = op.get_bind()
    postgresql.ENUM(name="entity_relation").drop(bind, checkfirst=True)
    postgresql.ENUM(name="entity_type").drop(bind, checkfirst=True)
