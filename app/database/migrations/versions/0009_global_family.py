"""Add global Family relationships.

Revision ID: 0009_global_family
Revises: 0008_appeals
"""
import sqlalchemy as sa
from alembic import op

revision = "0009_global_family"
down_revision = "0008_appeals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("family_relationships"):
        return
    op.create_table(
        "family_relationships",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("related_user_id", sa.BigInteger(), nullable=False),
        sa.Column("relation", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "related_user_id", "relation", name="uq_family_edge"),
    )
    op.create_index("ix_family_relationship_user", "family_relationships", ["user_id", "relation"])
    op.create_index("ix_family_relationship_related", "family_relationships", ["related_user_id", "relation"])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("family_relationships"):
        op.drop_index("ix_family_relationship_related", table_name="family_relationships")
        op.drop_index("ix_family_relationship_user", table_name="family_relationships")
        op.drop_table("family_relationships")
