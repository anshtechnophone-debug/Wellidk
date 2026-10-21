"""Persist AI request usage and token reservations.

Revision ID: 0004_ai_usage
Revises: 0003_word_games
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_ai_usage"
down_revision = "0003_word_games"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("ai_usage_events"):
        return
    op.create_table(
        "ai_usage_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("trigger_type", sa.String(24), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_usage_events_chat_id", "ai_usage_events", ["chat_id"])
    op.create_index("ix_ai_usage_events_created_at", "ai_usage_events", ["created_at"])
    op.create_index("ix_ai_usage_chat_created", "ai_usage_events", ["chat_id", "created_at"])
    op.create_index(
        "ix_ai_usage_user_created", "ai_usage_events", ["chat_id", "user_id", "created_at"]
    )


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("ai_usage_events"):
        op.drop_index("ix_ai_usage_user_created", table_name="ai_usage_events")
        op.drop_index("ix_ai_usage_chat_created", table_name="ai_usage_events")
        op.drop_index("ix_ai_usage_events_created_at", table_name="ai_usage_events")
        op.drop_index("ix_ai_usage_events_chat_id", table_name="ai_usage_events")
        op.drop_table("ai_usage_events")
