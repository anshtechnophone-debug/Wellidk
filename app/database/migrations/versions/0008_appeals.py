"""Add moderation appeal submissions and review state.

Revision ID: 0008_appeals
Revises: 0007_casual_games
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_appeals"
down_revision = "0007_casual_games"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("appeals"):
        return
    op.create_table(
        "appeals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("moderation_event_id", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reviewed_by", sa.BigInteger(), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["moderation_event_id"], ["moderation_events.id"], ondelete="SET NULL"
        ),
    )
    op.create_index("ix_appeals_chat_id", "appeals", ["chat_id"])
    op.create_index("ix_appeals_created_at", "appeals", ["created_at"])
    op.create_index("ix_appeals_group_created", "appeals", ["chat_id", "created_at"])
    op.create_index("ix_appeals_user_created", "appeals", ["chat_id", "user_id", "created_at"])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("appeals"):
        op.drop_index("ix_appeals_user_created", table_name="appeals")
        op.drop_index("ix_appeals_group_created", table_name="appeals")
        op.drop_index("ix_appeals_created_at", table_name="appeals")
        op.drop_index("ix_appeals_chat_id", table_name="appeals")
        op.drop_table("appeals")
