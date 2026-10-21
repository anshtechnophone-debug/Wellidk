"""Persist original permissions while XYRON has a member muted.

Revision ID: 0002_restriction_snapshots
Revises: 0001_initial
"""

import sqlalchemy as sa
from alembic import op

from app.database.models import json_value

revision = "0002_restriction_snapshots"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("restriction_snapshots"):
        return
    op.create_table(
        "restriction_snapshots",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("permissions", json_value, nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["groups.chat_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chat_id", "target_id"),
    )
    op.create_index(
        "ix_restriction_snapshots_group_user",
        "restriction_snapshots",
        ["chat_id", "target_id"],
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("restriction_snapshots"):
        op.drop_index("ix_restriction_snapshots_group_user", table_name="restriction_snapshots")
        op.drop_table("restriction_snapshots")
