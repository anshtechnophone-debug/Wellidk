"""Add persistent local casual game sessions and scores.

Revision ID: 0007_casual_games
Revises: 0006_group_policies
"""

import sqlalchemy as sa
from alembic import op

from app.database.models import json_value

revision = "0007_casual_games"
down_revision = "0006_group_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("casual_game_sessions"):
        op.create_table(
            "casual_game_sessions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("game_type", sa.String(24), nullable=False),
            sa.Column("active_key", sa.String(96), nullable=True),
            sa.Column("state", json_value, nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("active_key", name="uq_casual_game_active_key"),
        )
        op.create_index("ix_casual_game_sessions_chat_id", "casual_game_sessions", ["chat_id"])
        op.create_index(
            "ix_casual_game_active", "casual_game_sessions", ["chat_id", "game_type", "status"]
        )
    if not sa.inspect(op.get_bind()).has_table("casual_game_scores"):
        op.create_table(
            "casual_game_scores",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("session_id", sa.Integer(), nullable=False),
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("game_type", sa.String(24), nullable=False),
            sa.Column("points", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["session_id"], ["casual_game_sessions.id"], ondelete="CASCADE"
            ),
            sa.UniqueConstraint("session_id", name="uq_casual_game_score_session"),
        )
        op.create_index(
            "ix_casual_game_scores_board",
            "casual_game_scores",
            ["chat_id", "game_type", "points"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("casual_game_scores"):
        op.drop_index("ix_casual_game_scores_board", table_name="casual_game_scores")
        op.drop_table("casual_game_scores")
    if inspector.has_table("casual_game_sessions"):
        op.drop_index("ix_casual_game_active", table_name="casual_game_sessions")
        op.drop_index("ix_casual_game_sessions_chat_id", table_name="casual_game_sessions")
        op.drop_table("casual_game_sessions")
