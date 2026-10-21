"""Add per-group policy configuration.

Revision ID: 0006_group_policies
Revises: 0005_reports
"""

import sqlalchemy as sa
from alembic import op

from app.database.models import json_value

revision = "0006_group_policies"
down_revision = "0005_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("group_policies"):
        return
    op.create_table(
        "group_policies",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("policy", json_value, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["groups.chat_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chat_id"),
    )


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("group_policies"):
        op.drop_table("group_policies")
